const API = "/api";

const state = { bays: [], fleet: [], spots: [], system: {} };

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "status") loadStatus();
    if (tab.dataset.tab === "settings") loadSettings();
  });
});

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text() || res.statusText);
  return res.json();
}

async function loadBays() {
  state.bays = await api("/bays");
  renderBays();
}

function renderBays() {
  const container = document.getElementById("bay-list");
  if (!state.bays.length) {
    container.innerHTML = `<p class="hint">No bays configured yet. Add one or import from add-on config.</p>`;
    return;
  }

  container.innerHTML = state.bays
    .map(
      (bay) => `
    <article class="bay-card" data-id="${bay.id}">
      <div class="bay-card-header">
        <h3>${bay.name}</h3>
        <code>${bay.camera_entity_id}</code>
      </div>
      <div class="bay-preview" id="preview-${bay.id}">
        <span class="hint">No snapshot yet</span>
      </div>
      <div class="bay-actions">
        <button class="btn small refresh-snap" data-id="${bay.id}">Snapshot</button>
        <button class="btn small primary analyze-one" data-id="${bay.id}">Analyze</button>
        <button class="btn small edit-bay" data-id="${bay.id}">Edit</button>
        <button class="btn small danger delete-bay" data-id="${bay.id}">Delete</button>
      </div>
    </article>`
    )
    .join("");

  container.querySelectorAll(".refresh-snap").forEach((btn) => {
    btn.addEventListener("click", () => loadBaySnapshot(btn.dataset.id));
  });
  container.querySelectorAll(".analyze-one").forEach((btn) => {
    btn.addEventListener("click", () => analyzeBay(btn.dataset.id));
  });
  container.querySelectorAll(".edit-bay").forEach((btn) => {
    btn.addEventListener("click", () => editBay(btn.dataset.id));
  });
  container.querySelectorAll(".delete-bay").forEach((btn) => {
    btn.addEventListener("click", () => deleteBay(btn.dataset.id));
  });

  state.bays.forEach((bay) => loadBaySnapshot(bay.id, true));
}

async function loadBaySnapshot(bayId, silent = false) {
  const preview = document.getElementById(`preview-${bayId}`);
  if (!preview) return;
  try {
    const data = await api(`/snapshots/${bayId}/latest`);
    preview.innerHTML = `<img src="${data.url}?t=${Date.now()}" alt="Bay snapshot">`;
  } catch (err) {
    if (!silent) alert("Snapshot failed: " + err.message);
  }
}

async function analyzeBay(bayId) {
  try {
    await api(`/analyze/${bayId}`, { method: "POST" });
    await loadBaySnapshot(bayId);
    alert("Analysis complete. Check Live Status.");
  } catch (err) {
    alert("Analysis failed: " + err.message);
  }
}

document.getElementById("analyze-all").addEventListener("click", async () => {
  const btn = document.getElementById("analyze-all");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  try {
    const result = await api("/analyze", { method: "POST" });
    alert(`Done: ${result.analyzed}/${result.bays} bays` + (result.errors.length ? `\nErrors:\n${result.errors.join("\n")}` : ""));
    await loadBays();
  } catch (err) {
    alert("Analysis failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze all bays";
  }
});

document.getElementById("add-bay").addEventListener("click", () => {
  showModal(
    "Add parking bay",
    [
      { id: "bay-name", label: "Bay name", type: "text", placeholder: "Bay 1" },
      { id: "bay-entity", label: "Camera entity ID", type: "text", placeholder: "camera.parking_bay_1" },
      { id: "bay-order", label: "Capture order", type: "number", placeholder: "0" },
    ],
    async () => {
      await api("/bays", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("bay-name").value,
          camera_entity_id: document.getElementById("bay-entity").value,
          sort_order: parseInt(document.getElementById("bay-order").value || "0", 10),
        }),
      });
      hideModal();
      loadBays();
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
    ],
    async () => {
      await api(`/bays/${bayId}`, {
        method: "PUT",
        body: JSON.stringify({
          name: document.getElementById("bay-name").value,
          camera_entity_id: document.getElementById("bay-entity").value,
          sort_order: parseInt(document.getElementById("bay-order").value || "0", 10),
        }),
      });
      hideModal();
      loadBays();
    }
  );
}

async function deleteBay(bayId) {
  if (!confirm("Delete this bay?")) return;
  await api(`/bays/${bayId}`, { method: "DELETE" });
  loadBays();
}

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

async function loadStatus() {
  state.spots = await api("/spots");
  const grid = document.getElementById("status-grid");
  if (!state.spots.length) {
    grid.innerHTML = `<p class="hint">No results yet. Configure bays and run analysis.</p>`;
    return;
  }

  grid.innerHTML = state.spots
    .map((s) => {
      const confPct = Math.round((s.confidence || 0) * 100);
      return `
      <div class="status-card ${s.occupied ? "occupied" : "empty"}">
        <h4>${s.bay_name}</h4>
        <div class="meta">${s.camera_entity_id}</div>
        <div class="status-row"><span class="label">Status</span><span class="badge ${s.occupied ? "occupied" : "empty"}">${s.occupied ? "Occupied" : "Empty"}</span></div>
        <div class="status-row"><span class="label">Car #</span><span>${s.car_number ?? "—"}</span></div>
        <div class="status-row"><span class="label">ArUco ID</span><span><code>${s.aruco_id_detected ?? "—"}</code></span></div>
        <div class="status-row"><span class="label">Confidence</span><span>${confPct}%</span></div>
        <div class="confidence-bar"><span style="width:${confPct}%"></span></div>
        <div class="meta">${s.analyzed_at ? new Date(s.analyzed_at).toLocaleString() : ""}</div>
      </div>`;
    })
    .join("");
}

document.getElementById("refresh-status").addEventListener("click", loadStatus);

async function loadSettings() {
  state.system = await api("/status");
  document.getElementById("system-info").textContent = JSON.stringify(state.system, null, 2);
}

document.getElementById("import-addon-bays").addEventListener("click", async () => {
  const imported = await api("/import-addon-bays", { method: "POST" });
  alert(`Imported ${imported.length} bay(s)`);
  loadBays();
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
    await loadBays();
    await loadFleet();
  } catch (err) {
    document.querySelector("main").innerHTML = `<p class="error">Failed to connect: ${err.message}</p>`;
  }
}

init();
