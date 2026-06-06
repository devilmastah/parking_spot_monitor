/** Resolve paths for HA Ingress (absolute /api and /static break behind ingress proxy). */
function addonUrl(relativePath) {
  const base = window.location.href.endsWith("/")
    ? window.location.href
    : `${window.location.href}/`;
  return new URL(relativePath.replace(/^\//, ""), base).href;
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
  const rel = `api${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(addonUrl(rel), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text() || res.statusText);
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
        correctCar === "yes" ? "Correct car" : correctCar === "no" ? "Wrong car" : "Car check N/A";
      const correctClass =
        correctCar === "yes" ? "correct-yes" : correctCar === "no" ? "correct-no" : "correct-unknown";
      const img = bay.snapshot_url
        ? `<img src="${addonUrl(bay.snapshot_url)}?t=${Date.now()}" alt="${bay.bay_name}">`
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
  const list = document.getElementById("bay-config-list");
  if (!state.bays.length) {
    list.innerHTML = `<p class="hint">No bays yet.</p>`;
    return;
  }
  list.innerHTML = state.bays
    .map(
      (b) => `
    <div class="config-row">
      <div>
        <strong>${b.name}</strong><br>
        <code>${b.camera_entity_id}</code><br>
        <span class="hint">Expected car: ${b.expected_car_number ?? "not set"}</span>
      </div>
      <div class="config-actions">
        <button class="btn small bay-snapshot" data-id="${b.id}">Take snapshot</button>
        <button class="btn small edit-bay" data-id="${b.id}">Edit</button>
        <button class="btn small danger delete-bay" data-id="${b.id}">Delete</button>
      </div>
    </div>`
    )
    .join("");

  list.querySelectorAll(".edit-bay").forEach((btn) => btn.addEventListener("click", () => editBay(btn.dataset.id)));
  list.querySelectorAll(".delete-bay").forEach((btn) => btn.addEventListener("click", () => deleteBay(btn.dataset.id)));
  list.querySelectorAll(".bay-snapshot").forEach((btn) =>
    btn.addEventListener("click", () => takeSnapshot(btn.dataset.id, btn))
  );
}

document.getElementById("add-bay").addEventListener("click", () => {
  showModal(
    "Add parking bay",
    [
      { id: "bay-name", label: "Bay name", type: "text", placeholder: "Bay 1" },
      { id: "bay-entity", label: "Camera entity ID", type: "text", placeholder: "camera.parking_bay_1" },
      { id: "bay-order", label: "Capture order", type: "number", placeholder: "0" },
      { id: "bay-expected", label: "Expected car number (optional)", type: "number", placeholder: "1" },
    ],
    async () => {
      const expectedVal = document.getElementById("bay-expected").value;
      await api("/bays", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("bay-name").value,
          camera_entity_id: document.getElementById("bay-entity").value,
          sort_order: parseInt(document.getElementById("bay-order").value || "0", 10),
          expected_car_number: expectedVal === "" ? null : parseInt(expectedVal, 10),
        }),
      });
      hideModal();
      loadBayConfig();
      loadDashboard();
    }
  );
});

function editBay(bayId) {
  const bay = state.bays.find((b) => b.id === bayId);
  if (!bay) return;
  showModal(
    "Edit parking bay",
    [
      { id: "bay-name", label: "Bay name", type: "text", value: bay.name },
      { id: "bay-entity", label: "Camera entity ID", type: "text", value: bay.camera_entity_id },
      { id: "bay-order", label: "Capture order", type: "number", value: bay.sort_order },
      {
        id: "bay-expected",
        label: "Expected car number (optional)",
        type: "number",
        value: bay.expected_car_number ?? "",
      },
    ],
    async () => {
      const expectedVal = document.getElementById("bay-expected").value;
      await api(`/bays/${bayId}`, {
        method: "PUT",
        body: JSON.stringify({
          name: document.getElementById("bay-name").value,
          camera_entity_id: document.getElementById("bay-entity").value,
          sort_order: parseInt(document.getElementById("bay-order").value || "0", 10),
          expected_car_number: expectedVal === "" ? null : parseInt(expectedVal, 10),
        }),
      });
      hideModal();
      loadBayConfig();
      loadDashboard();
    }
  );
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
  const imported = await api("/import-addon-bays", { method: "POST" });
  alert(`Imported ${imported.length} bay(s)`);
  loadBayConfig();
  loadDashboard();
});

function showModal(title, fields, onConfirm) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = fields
    .map(
      (f) => `
    <label>${f.label}
      <input type="${f.type}" id="${f.id}" placeholder="${f.placeholder || ""}" value="${f.value ?? ""}">
    </label>`
    )
    .join("");
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
