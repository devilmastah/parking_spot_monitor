/** Build URLs under the Ingress prefix (HA injects <base href="http://.../">). */
function ingressUrl(relativePath) {
  const rel = relativePath.replace(/^\//, "").replace(/\/+$/, "");
  const prefix = window.INGRESS_PREFIX;
  if (prefix) {
    return `${prefix.replace(/\/$/, "")}/${rel}`;
  }
  return new URL(rel, window.location.href).href;
}

const REFRESH_MS = 30000;

const state = { bays: [], fleet: [], dashboard: [], system: {} };
let refreshTimer = null;

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "dashboard") {
      loadDashboard();
      startAutoRefresh();
    } else {
      stopAutoRefresh();
      if (tab.dataset.tab === "bays") loadBayConfig();
      if (tab.dataset.tab === "settings") loadSettings();
    }
  });
});

async function api(path, options = {}) {
  const apiPath = `api${path.startsWith("/") ? path : `/${path}`}`.replace(/\/+$/, "");
  const res = await fetch(ingressUrl(apiPath), {
    headers: { "Content-Type": "application/json" },
    redirect: "manual",
    ...options,
  });
  if (res.status >= 300 && res.status < 400) {
    throw new Error(`Unexpected redirect (${res.status}) — update the add-on to the latest version.`);
  }
  if (!res.ok) {
    const text = await res.text();
    let message = text || res.statusText;
    try {
      const parsed = JSON.parse(text);
      if (parsed.detail) message = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch (_) {}
    throw new Error(message);
  }
  return res.json();
}

function formatTime(iso) {
  if (!iso) return "Never analyzed";
  return new Date(iso).toLocaleString();
}

function confPct(v) {
  return Math.round((v || 0) * 100);
}

// ── Dashboard ─────────────────────────────────────────────────────────────

async function loadDashboard() {
  state.dashboard = await api("/dashboard");
  renderDashboard();
}

function renderDashboard() {
  const grid = document.getElementById("dashboard-grid");
  if (!state.dashboard.length) {
    grid.innerHTML = `<p class="hint">No bays configured. Go to <strong>Configure bays</strong> or import from add-on config.</p>`;
    return;
  }

  grid.innerHTML = state.dashboard
    .map((bay) => {
      const pct = confPct(bay.confidence);
      const hasResult = bay.analyzed_at != null;
      const occupied = bay.occupied === true;
      const statusClass = !hasResult ? "unknown" : occupied ? "occupied" : "empty";
      const unknownMarker = hasResult && occupied && bay.aruco_id_detected != null && bay.car_number == null;
      const statusLabel = !hasResult
        ? "No data"
        : unknownMarker
          ? `Unknown marker (ID ${bay.aruco_id_detected})`
          : occupied
            ? "Occupied"
            : "Empty";
      const correctCar = bay.correct_car || "uncertain";
      const correctLabel =
        correctCar === "yes" ? "Correct car" : correctCar === "no" ? "Wrong car" : "Assign expected car";
      const correctClass =
        correctCar === "yes" ? "correct-yes" : correctCar === "no" ? "correct-no" : "correct-unknown";
      const expectedHint =
        bay.expected_car_number == null
          ? `<p class="hint warn">No expected car — assign in <strong>Configure bays</strong></p>`
          : "";
      const img = bay.snapshot_url
        ? `<img src="${ingressUrl(bay.snapshot_url)}?t=${Date.now()}" alt="${bay.bay_name}">`
        : `<span class="no-image">No snapshot yet</span>`;

      return `
      <article class="dash-card ${statusClass}" data-id="${bay.bay_id}">
        <div class="dash-image">${img}</div>
        <div class="dash-body">
          <div class="dash-header">
            <h3>${bay.bay_name}</h3>
            <span class="badge ${statusClass}">${statusLabel}</span>
          </div>
          <code class="entity-id">${bay.camera_entity_id}</code>
          <div class="dash-stats">
            <div><span class="label">Expected car</span><strong>${bay.expected_car_number ?? "—"}</strong></div>
            <div><span class="label">Detected car</span><strong>${bay.car_number ?? "—"}</strong></div>
            <div><span class="label">ArUco ID</span><strong>${bay.aruco_id_detected ?? "—"}</strong></div>
            <div><span class="label">Confidence</span><strong>${hasResult ? pct + "%" : "—"}</strong></div>
          </div>
          ${expectedHint}
          ${bay.expected_car_number != null && hasResult ? `<span class="badge ${correctClass}">${correctLabel}</span>` : ""}
          <div class="confidence-bar"><span style="width:${pct}%"></span></div>
          <div class="dash-meta">${formatTime(bay.analyzed_at)}</div>
          <div class="dash-actions">
            <button class="btn small dash-snapshot" data-id="${bay.bay_id}">Take snapshot</button>
            <button class="btn small primary dash-analyze" data-id="${bay.bay_id}">Analyze</button>
          </div>
        </div>
      </article>`;
    })
    .join("");

  grid.querySelectorAll(".dash-analyze").forEach((btn) => {
    btn.addEventListener("click", () => analyzeBay(btn.dataset.id));
  });
  grid.querySelectorAll(".dash-snapshot").forEach((btn) => {
    btn.addEventListener("click", () => takeSnapshot(btn.dataset.id, btn));
  });
}

async function analyzeBay(bayId) {
  const btn = document.querySelector(`.dash-analyze[data-id="${bayId}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    await api(`/analyze/${bayId}`, { method: "POST" });
    await loadDashboard();
  } catch (err) {
    alert("Analysis failed: " + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Analyze"; }
  }
}

async function takeSnapshot(bayId, btn) {
  const button = btn || document.querySelector(`.dash-snapshot[data-id="${bayId}"]`);
  const label = button?.textContent || "Take snapshot";
  if (button) {
    button.disabled = true;
    button.textContent = "Capturing…";
  }
  try {
    await api(`/snapshots/${bayId}/capture`, { method: "POST" });
    await loadDashboard();
  } catch (err) {
    alert("Snapshot failed: " + err.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = label;
    }
  }
}

document.getElementById("refresh-dashboard").addEventListener("click", loadDashboard);

document.getElementById("analyze-all-dash").addEventListener("click", async () => {
  const btn = document.getElementById("analyze-all-dash");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  try {
    const result = await api("/analyze", { method: "POST" });
    if (result.errors?.length) {
      alert(`Done ${result.analyzed}/${result.bays} bays.\n\nErrors:\n${result.errors.join("\n")}`);
    }
    await loadDashboard();
  } catch (err) {
    alert("Analysis failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze all";
  }
});

function startAutoRefresh() {
  stopAutoRefresh();
  if (!document.getElementById("auto-refresh").checked) return;
  refreshTimer = setInterval(() => {
    if (document.getElementById("tab-dashboard").classList.contains("active")) {
      loadDashboard();
    }
  }, REFRESH_MS);
}

function stopAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;
}

document.getElementById("auto-refresh").addEventListener("change", () => {
  if (document.getElementById("tab-dashboard").classList.contains("active")) {
    startAutoRefresh();
  }
});

// ── Bay config ──────────────────────────────────────────────────────────────

async function loadBayConfig() {
  state.bays = await api("/bays");
  state.fleet = await api("/fleet");
  const tbody = document.getElementById("bay-config-list");
  if (!state.bays.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="hint">No bays yet. Import from Settings or click Add bay.</td></tr>`;
    return;
  }

  const fleetOptions = (selected) => {
    let html = `<option value="">Not assigned</option>`;
    for (const car of state.fleet) {
      const sel = selected === car.car_number ? " selected" : "";
      html += `<option value="${car.car_number}"${sel}>Car ${car.car_number} (ArUco ${car.aruco_id})</option>`;
    }
    return html;
  };

  tbody.innerHTML = state.bays
    .map(
      (b) => `
    <tr>
      <td><strong>${b.name}</strong></td>
      <td><code>${b.camera_entity_id}</code></td>
      <td>
        <select class="expected-car-select" data-id="${b.id}" aria-label="Expected car for ${b.name}">
          ${fleetOptions(b.expected_car_number)}
        </select>
      </td>
      <td class="config-actions">
        <button class="btn small bay-snapshot" data-id="${b.id}">Snapshot</button>
        <button class="btn small edit-bay" data-id="${b.id}">Edit</button>
        <button class="btn small danger delete-bay" data-id="${b.id}">Delete</button>
      </td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".expected-car-select").forEach((sel) => {
    sel.addEventListener("change", () => assignExpectedCar(sel.dataset.id, sel));
  });
  tbody.querySelectorAll(".edit-bay").forEach((btn) => btn.addEventListener("click", () => editBay(btn.dataset.id)));
  tbody.querySelectorAll(".delete-bay").forEach((btn) => btn.addEventListener("click", () => deleteBay(btn.dataset.id)));
  tbody.querySelectorAll(".bay-snapshot").forEach((btn) =>
    btn.addEventListener("click", () => takeSnapshot(btn.dataset.id, btn))
  );
}

async function assignExpectedCar(bayId, selectEl) {
  const value = selectEl.value;
  const expected_car_number = value === "" ? null : parseInt(value, 10);
  selectEl.disabled = true;
  try {
    await api(`/bays/${bayId}/expected-car`, {
      method: "PATCH",
      body: JSON.stringify({ expected_car_number }),
    });
    await loadBayConfig();
    if (document.getElementById("tab-dashboard").classList.contains("active")) {
      await loadDashboard();
    }
  } catch (err) {
    alert("Could not save expected car: " + err.message);
    await loadBayConfig();
  }
}

document.getElementById("add-bay").addEventListener("click", () => openBayModal());

function buildFleetOptions(selected) {
  let html = `<option value="">— Not assigned —</option>`;
  for (const car of state.fleet) {
    const sel = selected === car.car_number ? " selected" : "";
    html += `<option value="${car.car_number}"${sel}>Car ${car.car_number} (ArUco ID ${car.aruco_id})</option>`;
  }
  if (!state.fleet.length) {
    html += `<option value="" disabled>Add cars on the Fleet tab first</option>`;
  }
  return html;
}

async function openBayModal(bay = null) {
  state.fleet = await api("/fleet");
  if (!bay) {
    state.bays = await api("/bays");
  }
  const isEdit = bay != null;
  const nextIndex = state.bays.length;
  const defaultEntity = `camera.parking_bay_${nextIndex + 1}`;
  showModal(
    isEdit ? "Edit parking bay" : "Add parking bay",
    [
      {
        id: "bay-name",
        label: "Bay name",
        type: "text",
        placeholder: `Bay ${nextIndex + 1}`,
        value: bay?.name ?? "",
      },
      {
        id: "bay-entity",
        label: "Camera entity ID",
        type: "text",
        placeholder: defaultEntity,
        hint: isEdit
          ? "Home Assistant camera entity for this ESP32-CAM. Must be unique per bay."
          : `Must match your ESP32 camera in HA (e.g. ${defaultEntity}). Each bay needs a different camera.`,
        value: bay?.camera_entity_id ?? "",
      },
      {
        id: "bay-expected",
        label: "Expected car for this spot",
        type: "select",
        hint: "Which fleet car should be parked here? Required for MQTT correct_car.",
        optionsHtml: buildFleetOptions(bay?.expected_car_number ?? null),
      },
      {
        id: "bay-order",
        label: "Snapshot order",
        type: "number",
        placeholder: "0",
        hint: "Order when Analyze all runs: 0 = first snapshot, 1 = second, etc.",
        value: bay?.sort_order ?? nextIndex,
      },
    ],
    async () => {
      const expectedVal = document.getElementById("bay-expected").value;
      const cameraEntity = document.getElementById("bay-entity").value.trim();
      if (!cameraEntity) {
        alert("Camera entity ID is required.");
        return;
      }
      const body = {
        name: document.getElementById("bay-name").value.trim() || cameraEntity,
        camera_entity_id: cameraEntity,
        sort_order: parseInt(document.getElementById("bay-order").value || "0", 10),
        expected_car_number: expectedVal === "" ? null : parseInt(expectedVal, 10),
      };
      try {
        if (isEdit) {
          await api(`/bays/${bay.id}`, { method: "PUT", body: JSON.stringify(body) });
        } else {
          await api("/bays", { method: "POST", body: JSON.stringify(body) });
        }
        hideModal();
        loadBayConfig();
        loadDashboard();
      } catch (err) {
        alert(isEdit ? "Could not update bay: " : "Could not add bay: " + err.message);
      }
    }
  );
}

function editBay(bayId) {
  const bay = state.bays.find((b) => b.id === bayId);
  if (!bay) return;
  openBayModal(bay);
}

async function deleteBay(bayId) {
  if (!confirm("Delete this bay?")) return;
  await api(`/bays/${bayId}`, { method: "DELETE" });
  loadBayConfig();
  loadDashboard();
}

// ── Fleet ───────────────────────────────────────────────────────────────────

async function loadFleet() {
  state.fleet = await api("/fleet");
  const tbody = document.querySelector("#fleet-table tbody");
  tbody.innerHTML = state.fleet
    .map(
      (car) => `
    <tr>
      <td>${car.car_number}</td>
      <td><code>${car.aruco_id}</code></td>
      <td>${car.notes || "—"}</td>
      <td><button class="btn small danger" data-num="${car.car_number}">Delete</button></td>
    </tr>`
    )
    .join("");
  tbody.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Remove this car?")) return;
      await api(`/fleet/${btn.dataset.num}`, { method: "DELETE" });
      loadFleet();
    });
  });
}

document.getElementById("add-car").addEventListener("click", () => {
  showModal(
    "Add fleet car",
    [
      { id: "car-number", label: "Car number", type: "number", placeholder: "1" },
      { id: "car-aruco", label: "ArUco marker ID", type: "number", placeholder: "1" },
      { id: "car-notes", label: "Notes", type: "text", placeholder: "Red van" },
    ],
    async () => {
      await api("/fleet", {
        method: "POST",
        body: JSON.stringify({
          car_number: parseInt(document.getElementById("car-number").value, 10),
          aruco_id: parseInt(document.getElementById("car-aruco").value, 10),
          notes: document.getElementById("car-notes").value,
        }),
      });
      hideModal();
      loadFleet();
    }
  );
});

// ── Settings ────────────────────────────────────────────────────────────────

async function loadSettings() {
  state.system = await api("/status");
  document.getElementById("system-info").textContent = JSON.stringify(state.system, null, 2);
}

document.getElementById("import-addon-bays").addEventListener("click", async () => {
  const result = await api("/import-addon-bays", { method: "POST" });
  const mqtt = result.mqtt || {};
  alert(
    `Imported ${result.bays?.length ?? 0} bay(s)` +
      (mqtt.published ? `\nMQTT discovery published for ${mqtt.published} bay(s).` : mqtt.error ? `\nMQTT: ${mqtt.error}` : "")
  );
  loadBayConfig();
  loadDashboard();
});

document.getElementById("mqtt-publish").addEventListener("click", async () => {
  const btn = document.getElementById("mqtt-publish");
  btn.disabled = true;
  btn.textContent = "Publishing…";
  try {
    const result = await api("/mqtt/publish", { method: "POST" });
    if (result.error) {
      alert("MQTT publish failed: " + result.error);
    } else {
      alert(`Published MQTT discovery for ${result.published} bay(s).\n\nCheck Settings → Devices & services → MQTT.`);
    }
  } catch (err) {
    alert("MQTT publish failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Publish MQTT discovery";
  }
});

function renderModalField(f) {
  const hint = f.hint ? `<span class="field-hint">${f.hint}</span>` : "";
  if (f.type === "select") {
    return `
    <label class="modal-field">
      <span class="field-label">${f.label}</span>
      ${hint}
      <select id="${f.id}">${f.optionsHtml || ""}</select>
    </label>`;
  }
  return `
    <label class="modal-field">
      <span class="field-label">${f.label}</span>
      ${hint}
      <input type="${f.type}" id="${f.id}" placeholder="${f.placeholder || ""}" value="${f.value ?? ""}">
    </label>`;
}

function showModal(title, fields, onConfirm) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = fields.map(renderModalField).join("");
  document.getElementById("modal").classList.remove("hidden");
  const confirm = document.getElementById("modal-confirm");
  const clone = confirm.cloneNode(true);
  confirm.parentNode.replaceChild(clone, confirm);
  clone.addEventListener("click", onConfirm);
}

function hideModal() {
  document.getElementById("modal").classList.add("hidden");
}

document.getElementById("modal-cancel").addEventListener("click", hideModal);

async function init() {
  try {
    state.system = await api("/status");
    await loadDashboard();
    await loadFleet();
    startAutoRefresh();
  } catch (err) {
    document.querySelector("main").innerHTML = `<p class="error">Failed to connect: ${err.message}</p>`;
  }
}

init();
