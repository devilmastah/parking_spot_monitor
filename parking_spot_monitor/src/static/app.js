const API = "/api";

const state = {
  cameras: [],
  zones: [],
  fleet: [],
  spots: [],
  selectedCameraId: null,
  snapshotUrl: null,
  image: null,
  drawingPoints: [],
  drawingMode: false,
  editingZoneId: null,
  selectedZoneId: null,
};

const canvas = document.getElementById("zone-canvas");
const ctx = canvas.getContext("2d");

// --- Tabs ---
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
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  return res.json();
}

// --- Cameras ---
async function loadCameras() {
  state.cameras = await api("/cameras");
  const select = document.getElementById("camera-select");
  select.innerHTML = state.cameras
    .map((c) => `<option value="${c.id}">${c.name} (${c.entity_id})</option>`)
    .join("");

  if (state.cameras.length && !state.selectedCameraId) {
    state.selectedCameraId = state.cameras[0].id;
  }
  if (state.selectedCameraId) {
    select.value = state.selectedCameraId;
    await loadSnapshot();
    await loadZones();
  }
}

document.getElementById("camera-select").addEventListener("change", async (e) => {
  state.selectedCameraId = e.target.value;
  state.drawingPoints = [];
  state.editingZoneId = null;
  state.selectedZoneId = null;
  hideZoneForm();
  await loadSnapshot();
  await loadZones();
});

async function loadSnapshot() {
  if (!state.selectedCameraId) return;
  const data = await api(`/snapshots/${state.selectedCameraId}/latest`);
  state.snapshotUrl = data.url + "?t=" + Date.now();
  await new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      state.image = img;
      resizeCanvas();
      drawCanvas();
      resolve();
    };
    img.onerror = reject;
    img.src = state.snapshotUrl;
  });
}

function resizeCanvas() {
  if (!state.image) return;
  const maxW = canvas.parentElement.clientWidth - 2;
  const scale = Math.min(1, maxW / state.image.width);
  canvas.width = state.image.width * scale;
  canvas.height = state.image.height * scale;
}

function toNormalized(x, y) {
  return { x: x / canvas.width, y: y / canvas.height };
}

function toCanvas(norm) {
  return { x: norm.x * canvas.width, y: norm.y * canvas.height };
}

function drawPolygon(points, stroke, fill, label) {
  if (!points.length) return;
  ctx.beginPath();
  const first = toCanvas(points[0]);
  ctx.moveTo(first.x, first.y);
  points.slice(1).forEach((p) => {
    const c = toCanvas(p);
    ctx.lineTo(c.x, c.y);
  });
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2;
  ctx.stroke();

  if (label) {
    const cx = points.reduce((s, p) => s + p.x, 0) / points.length;
    const cy = points.reduce((s, p) => s + p.y, 0) / points.length;
    const c = toCanvas({ x: cx, y: cy });
    ctx.fillStyle = "#fff";
    ctx.font = "bold 14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(label, c.x, c.y);
  }
}

function drawCanvas() {
  if (!state.image) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);

  state.zones.forEach((zone) => {
    const isSelected = zone.id === state.selectedZoneId;
    drawPolygon(
      zone.points,
      isSelected ? "#fbbf24" : "#60a5fa",
      isSelected ? "rgba(251, 191, 36, 0.3)" : "rgba(59, 130, 246, 0.25)",
      zone.name
    );
  });

  if (state.drawingPoints.length) {
    ctx.beginPath();
    const first = toCanvas(state.drawingPoints[0]);
    ctx.moveTo(first.x, first.y);
    state.drawingPoints.slice(1).forEach((p) => {
      const c = toCanvas(p);
      ctx.lineTo(c.x, c.y);
    });
    ctx.strokeStyle = "#fbbf24";
    ctx.lineWidth = 2;
    ctx.stroke();

    state.drawingPoints.forEach((p, i) => {
      const c = toCanvas(p);
      ctx.beginPath();
      ctx.arc(c.x, c.y, i === 0 ? 8 : 5, 0, Math.PI * 2);
      ctx.fillStyle = i === 0 ? "#fbbf24" : "#fff";
      ctx.fill();
      ctx.strokeStyle = "#000";
      ctx.stroke();
    });
  }
}

window.addEventListener("resize", () => {
  resizeCanvas();
  drawCanvas();
});

canvas.addEventListener("click", (e) => {
  if (!state.drawingMode) return;
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const norm = toNormalized(x, y);

  if (state.drawingPoints.length >= 3) {
    const first = toCanvas(state.drawingPoints[0]);
    const dist = Math.hypot(x - first.x, y - first.y);
    if (dist < 12) {
      showZoneForm();
      return;
    }
  }

  state.drawingPoints.push(norm);
  drawCanvas();
});

canvas.addEventListener("dblclick", () => {
  if (state.drawingMode && state.drawingPoints.length >= 3) {
    showZoneForm();
  }
});

function showZoneForm() {
  document.getElementById("zone-form").classList.remove("hidden");
  document.getElementById("new-zone").classList.add("hidden");
  document.getElementById("clear-drawing").classList.remove("hidden");
  const title = document.getElementById("zone-form-title");
  title.textContent = state.editingZoneId ? "Edit Zone" : "New Zone";
  if (!state.editingZoneId) {
    document.getElementById("zone-name").value = `Spot ${state.zones.length + 1}`;
  }
}

function hideZoneForm() {
  document.getElementById("zone-form").classList.add("hidden");
  document.getElementById("new-zone").classList.remove("hidden");
  document.getElementById("clear-drawing").classList.add("hidden");
  state.drawingMode = false;
  state.drawingPoints = [];
  state.editingZoneId = null;
  drawCanvas();
}

document.getElementById("new-zone").addEventListener("click", () => {
  state.drawingMode = true;
  state.drawingPoints = [];
  state.editingZoneId = null;
  document.getElementById("zone-hint").textContent =
    "Click to add points. Double-click or click the first point to finish.";
});

document.getElementById("clear-drawing").addEventListener("click", hideZoneForm);
document.getElementById("cancel-zone").addEventListener("click", hideZoneForm);

document.getElementById("zone-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("zone-name").value.trim();
  const points = state.editingZoneId
    ? state.zones.find((z) => z.id === state.editingZoneId)?.points || state.drawingPoints
    : state.drawingPoints;

  if (points.length < 3) {
    alert("A zone needs at least 3 points");
    return;
  }

  const body = {
    camera_id: state.selectedCameraId,
    name,
    points,
    sort_order: state.zones.length,
  };

  if (state.editingZoneId) {
    await api(`/zones/${state.editingZoneId}`, { method: "PUT", body: JSON.stringify(body) });
  } else {
    await api("/zones", { method: "POST", body: JSON.stringify(body) });
  }

  hideZoneForm();
  await loadZones();
});

async function loadZones() {
  if (!state.selectedCameraId) return;
  state.zones = await api(`/zones?camera_id=${state.selectedCameraId}`);
  renderZoneList();
  drawCanvas();
}

function renderZoneList() {
  const list = document.getElementById("zone-list");
  list.innerHTML = state.zones
    .map(
      (z) => `
    <li data-id="${z.id}" class="${z.id === state.selectedZoneId ? "selected" : ""}">
      <span>${z.name}</span>
      <span>
        <button class="btn small edit-zone" data-id="${z.id}">Edit</button>
        <button class="btn small danger delete-zone" data-id="${z.id}">Del</button>
      </span>
    </li>`
    )
    .join("");

  list.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", (e) => {
      if (e.target.tagName === "BUTTON") return;
      state.selectedZoneId = li.dataset.id;
      renderZoneList();
      drawCanvas();
    });
  });

  list.querySelectorAll(".edit-zone").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const zone = state.zones.find((z) => z.id === btn.dataset.id);
      state.editingZoneId = zone.id;
      state.drawingPoints = [...zone.points];
      state.drawingMode = true;
      document.getElementById("zone-name").value = zone.name;
      showZoneForm();
      drawCanvas();
    });
  });

  list.querySelectorAll(".delete-zone").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this zone?")) return;
      await api(`/zones/${btn.dataset.id}`, { method: "DELETE" });
      await loadZones();
    });
  });
}

document.getElementById("refresh-snapshot").addEventListener("click", loadSnapshot);

document.getElementById("analyze-now").addEventListener("click", async () => {
  const btn = document.getElementById("analyze-now");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  try {
    await api(`/analyze/${state.selectedCameraId}`, { method: "POST" });
    alert("Analysis complete. Check the Live Status tab.");
  } catch (err) {
    alert("Analysis failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze Now";
  }
});

// --- Fleet ---
async function loadFleet() {
  state.fleet = await api("/fleet");
  const tbody = document.querySelector("#fleet-table tbody");
  tbody.innerHTML = state.fleet
    .map(
      (car) => `
    <tr>
      <td>${car.car_number}</td>
      <td><code>${car.license_plate}</code></td>
      <td>${car.notes || "—"}</td>
      <td><button class="btn small danger" data-num="${car.car_number}">Delete</button></td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Remove this car from fleet?")) return;
      await api(`/fleet/${btn.dataset.num}`, { method: "DELETE" });
      loadFleet();
    });
  });
}

document.getElementById("add-car").addEventListener("click", () => {
  showModal("Add Fleet Car", [
    { id: "car-number", label: "Car Number", type: "number", placeholder: "1" },
    { id: "car-plate", label: "License Plate", type: "text", placeholder: "AB-123-CD" },
    { id: "car-notes", label: "Notes (optional)", type: "text", placeholder: "Red van" },
  ], async () => {
    const car_number = parseInt(document.getElementById("car-number").value, 10);
    const license_plate = document.getElementById("car-plate").value;
    const notes = document.getElementById("car-notes").value;
    await api("/fleet", {
      method: "POST",
      body: JSON.stringify({ car_number, license_plate, notes }),
    });
    hideModal();
    loadFleet();
  });
});

// --- Status ---
async function loadStatus() {
  state.spots = await api("/spots");
  const grid = document.getElementById("status-grid");

  if (!state.spots.length) {
    grid.innerHTML = `<p class="hint">No spot data yet. Configure zones and run analysis.</p>`;
    return;
  }

  grid.innerHTML = state.spots
    .map((s) => {
      const confPct = Math.round((s.confidence || 0) * 100);
      return `
      <div class="status-card ${s.occupied ? "occupied" : "empty"}">
        <h4>${s.zone_name}</h4>
        <div class="meta">${s.camera_name}</div>
        <div class="status-row">
          <span class="label">Status</span>
          <span class="badge ${s.occupied ? "occupied" : "empty"}">${s.occupied ? "Occupied" : "Empty"}</span>
        </div>
        <div class="status-row">
          <span class="label">Car #</span>
          <span>${s.car_number ?? "—"}</span>
        </div>
        <div class="status-row">
          <span class="label">Plate read</span>
          <span><code>${s.plate_read || "—"}</code></span>
        </div>
        <div class="status-row">
          <span class="label">Matched</span>
          <span><code>${s.plate_matched || "—"}</code></span>
        </div>
        <div class="status-row">
          <span class="label">Confidence</span>
          <span>${confPct}%</span>
        </div>
        <div class="confidence-bar"><span style="width:${confPct}%"></span></div>
        <div class="meta" style="margin-top:0.5rem">${s.analyzed_at ? new Date(s.analyzed_at).toLocaleString() : ""}</div>
      </div>`;
    })
    .join("");
}

document.getElementById("refresh-status").addEventListener("click", loadStatus);

// --- Settings ---
async function loadSettings() {
  const cameras = await api("/cameras");
  document.getElementById("camera-list").innerHTML = cameras
    .map(
      (c) => `
    <li>
      <span><strong>${c.name}</strong> — <code>${c.entity_id}</code></span>
      <button class="btn small danger" data-id="${c.id}">Delete</button>
    </li>`
    )
    .join("");

  document.querySelectorAll("#camera-list button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete camera and its zones?")) return;
      await api(`/cameras/${btn.dataset.id}`, { method: "DELETE" });
      loadSettings();
      loadCameras();
    });
  });

  const info = await api("/status");
  document.getElementById("system-info").textContent = JSON.stringify(info, null, 2);
}

document.getElementById("camera-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("cam-name").value;
  const entity_id = document.getElementById("cam-entity").value;
  await api("/cameras", { method: "POST", body: JSON.stringify({ name, entity_id }) });
  e.target.reset();
  loadSettings();
  loadCameras();
});

document.getElementById("import-addon-cams").addEventListener("click", async () => {
  const imported = await api("/import-addon-cameras", { method: "POST" });
  alert(`Imported ${imported.length} camera(s)`);
  loadSettings();
  loadCameras();
});

// --- Modal ---
function showModal(title, fields, onConfirm) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = fields
    .map(
      (f) => `
    <label>${f.label}
      <input type="${f.type}" id="${f.id}" placeholder="${f.placeholder || ""}">
    </label>`
    )
    .join("");
  document.getElementById("modal").classList.remove("hidden");

  const confirm = document.getElementById("modal-confirm");
  const newConfirm = confirm.cloneNode(true);
  confirm.parentNode.replaceChild(newConfirm, confirm);
  newConfirm.addEventListener("click", onConfirm);
}

function hideModal() {
  document.getElementById("modal").classList.add("hidden");
}

document.getElementById("modal-cancel").addEventListener("click", hideModal);

// --- Init ---
async function init() {
  try {
    await loadCameras();
    await loadFleet();
  } catch (err) {
    console.error(err);
    document.querySelector("main").innerHTML =
      `<p style="color:#ef4444">Failed to connect to API: ${err.message}</p>`;
  }
}

init();
