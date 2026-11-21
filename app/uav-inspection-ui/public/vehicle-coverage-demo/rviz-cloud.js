function createRvizCloud({ points, firstSeen, planned, viewpointIds }, recordedRun) {
  const { coveredPoints: coverageCounts, cameraPositions } = recordedRun;
  const canvas = document.getElementById("rviz-cloud");
  const context = canvas.getContext("2d");
  if (!context || points.length !== firstSeen.length || !points.length) return null;

  const counts = coverageCounts.map((_, step) => firstSeen.filter(seen => seen >= 0 && seen <= step).length);
  if (!counts.every((count, step) => count === coverageCounts[step])) return null;

  const bounds = [0, 1, 2].map(axis => {
    const values = points.map(point => point[axis]);
    return [Math.min(...values), Math.max(...values)];
  });
  const center = bounds.map(([min, max]) => (min + max) / 2);
  const radius = Math.hypot(...bounds.map(([min, max]) => (max - min) / 2));
  const relativePosition = point => point.map((value, axis) => value - center[axis]);
  const coordinates = points.map(relativePosition);
  const photoPositions = cameraPositions.map(relativePosition);
  const plannedPositions = viewpointIds?.length === recordedRun.viewpointIds.length &&
    viewpointIds.every((id, index) => id === recordedRun.viewpointIds[index]) ? planned.map(relativePosition) : [];
  const routeRadius = Math.max(radius, ...[...photoPositions, ...plannedPositions].map(point => Math.hypot(...point)));
  const ground = -center[2];
  const gridRadius = Math.ceil(routeRadius) + 1;
  const routeToggle = document.getElementById("cloud-route-toggle");
  const photoKey = document.querySelector(".cloud-photo-key");
  const plannedKey = document.querySelector(".cloud-planned-key");
  const pointers = new Map();
  let showRoute = photoPositions.length > 0;
  let observation = 0;
  let routeTime = 0;
  let yaw = -0.78;
  let pitch = 0.43;
  let zoom = 1;
  let panX = 0;
  let panY = 0;

  function draw() {
    const { clientWidth: width, clientHeight: height } = canvas;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    const bitmapWidth = Math.round(width * ratio);
    const bitmapHeight = Math.round(height * ratio);
    if (canvas.width !== bitmapWidth || canvas.height !== bitmapHeight) {
      canvas.width = bitmapWidth;
      canvas.height = bitmapHeight;
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = "#222227";
    context.fillRect(0, 0, width, height);

    const cosYaw = Math.cos(yaw);
    const sinYaw = Math.sin(yaw);
    const cosPitch = Math.cos(pitch);
    const sinPitch = Math.sin(pitch);
    const sceneRadius = showRoute ? routeRadius : radius;
    const scale = Math.min(width, height) * (showRoute ? 0.58 : 0.52) * zoom / sceneRadius;
    const originX = width * (0.5 + panX);
    const originY = height * ((showRoute ? 0.57 : 0.5) + panY);
    const distance = (showRoute ? 4 : 3) * sceneRadius;

    function project([x, y, z]) {
      const right = x * cosYaw - y * sinYaw;
      const forward = x * sinYaw + y * cosYaw;
      const up = z * cosPitch - forward * sinPitch;
      const depth = forward * cosPitch + z * sinPitch;
      const perspective = distance / Math.max(distance - depth, sceneRadius);
      return [originX + right * scale * perspective, originY - up * scale * perspective, depth];
    }

    context.beginPath();
    for (let line = -gridRadius; line <= gridRadius; line++) {
      context.moveTo(...project([line, -gridRadius, ground]).slice(0, 2));
      context.lineTo(...project([line, gridRadius, ground]).slice(0, 2));
      context.moveTo(...project([-gridRadius, line, ground]).slice(0, 2));
      context.lineTo(...project([gridRadius, line, ground]).slice(0, 2));
    }
    context.strokeStyle = "#41434b";
    context.lineWidth = 0.5;
    context.stroke();

    const projected = coordinates.map((point, index) => [...project(point), firstSeen[index]]);
    projected.sort((a, b) => a[2] - b[2]);
    const size = Math.max(1.25, Math.min(2.8, width / 150));
    for (const [x, y, , seen] of projected) {
      context.fillStyle = seen >= 0 && seen <= observation ? "#29e997" : "#ff5a60";
      context.fillRect(x - size / 2, y - size / 2, size, size);
    }

    if (showRoute && observation > 0) {
      const photos = photoPositions.slice(0, observation).map(project);
      const goals = plannedPositions.slice(0, observation).map(project);
      const next=photoPositions[observation], start=recordedRun.times[observation-1], end=recordedRun.times[observation];
      if(next && end>start){const t=Math.max(0,Math.min(1,(routeTime-start)/(end-start))); const prev=photoPositions[observation-1]; photos.partial=project(prev.map((v,i)=>v+(next[i]-v)*t));}
      drawRoute(photos, goals, width, height);
    }
  }

  function drawRoute(photos, goals, width, height) {
    const routeColor = "#d260ff";
    if (photos.length > 1 || photos.partial) {
      context.beginPath();
      const [[startX, startY], ...remaining] = photos;
      context.moveTo(startX, startY);
      for (const [x, y] of remaining) context.lineTo(x, y);
      if(photos.partial) context.lineTo(...photos.partial.slice(0,2));
      context.strokeStyle = routeColor;
      context.lineWidth = 2;
      context.stroke();
    }

    function dot(x, y, size, color) {
      context.beginPath();
      context.arc(x, y, size, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
    }

    for (const [x, y] of goals) dot(x, y, 2.4, "#4ee9f8");
    for (const [index, [x, y]] of photos.entries()) {
      if (index + 1 === observation) dot(x, y, 6, "#ffe45c");
      dot(x, y, 4, routeColor);
    }

    context.font = "600 11px system-ui";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const labels = [];
    const offsets = [[0, -12], [11, -8], [-11, -8], [10, 9], [-10, 9], [0, 12], [0, -24]];
    for (const [index, [x, y]] of photos.entries()) {
      if (x < 0 || x > width || y < 0 || y > height) continue;
      const position = offsets.map(([dx, dy]) => [x + dx, y + dy]).find(([lx, ly]) =>
        lx >= 7 && lx <= width - 7 && ly >= 7 && ly <= height - 7 &&
        labels.every(([px, py]) => Math.abs(lx - px) > 10 || Math.abs(ly - py) > 12) &&
        photos.every(([px, py]) => Math.hypot(lx - px, ly - py) > 8));
      if (!position) continue;
      labels.push(position);
      const label = String(index + 1);
      context.strokeStyle = "#222227";
      context.lineWidth = 3;
      context.strokeText(label, ...position);
      context.fillStyle = index + 1 === observation ? "#ffe45c" : "#fff";
      context.fillText(label, ...position);
    }
  }

  function rotate(dx, dy) {
    yaw += dx * 0.012;
    pitch = Math.max(-1.5, Math.min(1.5, pitch + dy * 0.012));
  }

  function magnify(factor) {
    zoom = Math.max(0.55, Math.min(4, zoom * factor));
  }

  function reset() {
    yaw = -0.78;
    pitch = 0.43;
    zoom = 1;
    panX = 0;
    panY = 0;
    draw();
  }

  canvas.addEventListener("pointerdown", event => {
    if (event.button !== 0 || pointers.size >= 2) return;
    event.preventDefault();
    canvas.focus({ preventScroll: true });
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointermove", event => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    const [x, y] = previous;
    const dx = event.clientX - x;
    const dy = event.clientY - y;
    if (pointers.size === 2) {
      const [, [otherX, otherY]] = [...pointers].find(([id]) => id !== event.pointerId);
      const distance = Math.hypot(x - otherX, y - otherY);
      if (distance > 4) magnify(Math.hypot(event.clientX - otherX, event.clientY - otherY) / distance);
    } else if (event.shiftKey) {
      panX += dx / canvas.clientWidth;
      panY += dy / canvas.clientHeight;
    } else {
      rotate(dx, dy);
    }
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    draw();
  });
  canvas.addEventListener("lostpointercapture", event => pointers.delete(event.pointerId));
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    magnify(Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.06 : 0.0015)));
    draw();
  }, { passive: false });

  canvas.addEventListener("keydown", event => {
    switch (event.key) {
      case "ArrowLeft": rotate(-12, 0); break;
      case "ArrowRight": rotate(12, 0); break;
      case "ArrowUp": rotate(0, -12); break;
      case "ArrowDown": rotate(0, 12); break;
      case "+":
      case "=": magnify(1.15); break;
      case "-": magnify(1 / 1.15); break;
      default: return;
    }
    event.preventDefault();
    draw();
  });

  document.getElementById("cloud-reset").addEventListener("click", reset);
  function updateRouteVisibility() {
    routeToggle.setAttribute("aria-pressed", String(showRoute));
    photoKey.hidden = !showRoute;
    plannedKey.hidden = !showRoute || !plannedPositions.length;
    draw();
  }
  routeToggle.hidden = !photoPositions.length;
  routeToggle.addEventListener("click", () => {
    showRoute = !showRoute;
    updateRouteVisibility();
  });
  document.getElementById("cloud-controls").hidden = false;
  canvas.hidden = false;
  updateRouteVisibility();
  new ResizeObserver(draw).observe(canvas);
  return (step,time=recordedRun.times[step-1] ?? 0) => {
    observation = step;
    routeTime = time;
    draw();
  };
}
