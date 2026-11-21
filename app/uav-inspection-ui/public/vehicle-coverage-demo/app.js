const phaseNames = ["地图就绪至选点", "选点至实拍完成"];
const recordedRun = globalThis.recordedRun;
const { referencePoints, coveredPoints, coverage: coveragePercent, times: captureTimes, viewpointIds } = recordedRun;
const percentLabel = index => coveragePercent[index].toFixed(2) + "%";
const referenceLabel = referencePoints.toLocaleString("zh-CN");

const trendTable = document.getElementById("trend-table");
for (const [round, points] of coveredPoints.entries()) {
  const row = trendTable.insertRow();
  row.insertCell().textContent = round ? "第 " + round + " 次" : "任务起点";
  row.insertCell().textContent = round ? viewpointIds[round - 1] : "—";
  row.insertCell().textContent = round ? "+" + (points - coveredPoints[round - 1]).toLocaleString("zh-CN") : "—";
  row.insertCell().textContent = points.toLocaleString("zh-CN");
  row.insertCell().textContent = percentLabel(round);
  const cell = row.insertCell();
  if (round) {
    const link = document.createElement("a");
    link.href = "#observation-" + round;
    link.textContent = "查看";
    link.setAttribute("aria-label", "查看第 " + round + " 次观测");
    cell.append(link);
  }
}

const trend = document.getElementById("coverage-trend");
new ResizeObserver(([entry]) => {
  const { width, height } = entry.contentRect;
  if (!width || !height) return;
  const scale = window.devicePixelRatio || 1;
  trend.width = Math.round(width * scale);
  trend.height = Math.round(height * scale);
  const ctx = trend.getContext("2d");
  ctx.scale(scale, scale);
  const left = 45;
  const right = width - 22;
  const top = 25;
  const bottom = height - 31;
  const x = round => left + (right - left) * round / (coveredPoints.length - 1);
  const y = ratio => bottom - (bottom - top) * ratio;
  ctx.font = '12px system-ui';
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const percent of [0, 25, 50, 75, 100]) {
    const line = y(percent / 100);
    ctx.strokeStyle = "#dfe6f0";
    ctx.beginPath();
    ctx.moveTo(left, line);
    ctx.lineTo(right, line);
    ctx.stroke();
    ctx.fillStyle = "#536680";
    ctx.fillText(percent + "%", left - 8, line);
  }
  ctx.textAlign = "center";
  ctx.fillStyle = "#536680";
  coveredPoints.forEach((_, round) => ctx.fillText(round, x(round), bottom + 16));
  ctx.beginPath();
  ctx.moveTo(x(0), y(0));
  coveragePercent.slice(1).forEach((percent, index) => ctx.lineTo(x(index + 1), y(percent / 100)));
  ctx.strokeStyle = "#2456ed";
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.fillStyle = "#2456ed";
  coveragePercent.forEach((percent, round) => {
    ctx.beginPath();
    ctx.arc(x(round), y(percent / 100), 3.5, 0, 2 * Math.PI);
    ctx.fill();
  });
  ctx.textAlign = "right";
  ctx.fillText(percentLabel(coveragePercent.length - 1), right, y(coveragePercent.at(-1) / 100) - 12);
}).observe(trend);

function setupChoices(id, labels, render) {
  const container = document.getElementById(id);
  const buttons = labels.map((label, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tab";
    button.textContent = label;
    container.append(button);
    button.addEventListener("click", () => select(index));
    return button;
  });
  function select(index) {
    buttons.forEach((button, candidate) => button.setAttribute("aria-pressed", String(index === candidate)));
    render(index);
  }
  select(0);
}

function drawBars(id, rows, max) {
  const bars = rows.map(({ name, value, formatted, highlight }) => {
    const row = document.createElement("div");
    const label = document.createElement("div");
    label.className = "bar-label";
    const text = document.createElement("span");
    text.textContent = name;
    const number = document.createElement("strong");
    number.textContent = formatted;
    label.append(text, number);
    const track = document.createElement("div");
    track.className = "track";
    track.setAttribute("aria-hidden", "true");
    const fill = document.createElement("span");
    fill.className = highlight ? "fill highlight" : "fill";
    fill.style.width = (value / max * 100) + "%";
    track.append(fill);
    row.append(label, track);
    return row;
  });
  document.getElementById(id).replaceChildren(...bars);
}

const timings = recordedRun.diagnostics ?? [];
const roundTotal = document.getElementById("round-total");
document.getElementById("diagnostics-elapsed").textContent = recordedRun.elapsedSeconds.toFixed(1) + " s";
document.getElementById("diagnostics-result").textContent = `${captureTimes.length} 次有效观测 · 覆盖率 ${percentLabel(captureTimes.length)}`;
document.getElementById("diagnostics-empty").hidden = timings.length > 0;
if (timings.length) {
  roundTotal.hidden = false;
  const scale = Math.max(1, ...timings.flatMap(({ seconds }) => seconds));
  const labels = timings.map((_, index) => `视点 ${index + 1}`);
  setupChoices("rounds", labels, index => {
    const { seconds, viewpointId } = timings[index];
    const total = seconds.reduce((sum, value) => sum + value, 0);
    roundTotal.textContent = `#${viewpointId} · ${total.toFixed(2)} s`;
    drawBars("time-bars", seconds.map((value, phase) => ({
      name: phaseNames[phase],
      value,
      formatted: value.toFixed(2) + " s",
      highlight: phase === 1,
    })), scale);
  });
}

const views = [...document.querySelectorAll(".module-view")];
const links = document.querySelectorAll("nav a");
const video = document.getElementById("replay-video");
const flightVideo = document.createElement('video');
flightVideo.src = 'assets/suv-flight-planning.mp4';
flightVideo.preload = 'auto'; flightVideo.muted = true; flightVideo.playsInline = true;
flightVideo.hidden = true; document.body.append(flightVideo);
function syncFlight(force = false) {
  flightVideo.playbackRate = video.playbackRate;
  if (flightVideo.readyState && (force || Math.abs(flightVideo.currentTime-video.currentTime) > 0.12)) flightVideo.currentTime = video.currentTime;
}
video.addEventListener('play', () => { syncFlight(true); flightVideo.play().catch(()=>{}); });
video.addEventListener('pause', () => { flightVideo.pause(); syncFlight(true); });
video.addEventListener('seeked', () => syncFlight(true));
video.addEventListener('ratechange', () => syncFlight());
flightVideo.addEventListener('seeked', () => { if (video.paused) updateReplay(); });
flightVideo.addEventListener('loadeddata', () => { syncFlight(true); updateReplay(); });
const cameraVideoContext = document.getElementById("camera-video-frame").getContext("2d");
const cameraInsetContext = document.getElementById("camera-inset-frame").getContext("2d");
const cameraCrop = [0, 0, 640, 480];
if (recordedRun.cameraVideo) {
  video.src = "assets/suv-camera-planning.mp4";
  cameraVideoContext.canvas.hidden = false;
  document.body.classList.add("continuous-camera");
}
const home = document.getElementById("home");
const vehicleModule = document.getElementById("vehicle-module");

// Pixel regions in the composed 1280 × 720 recording.
const flightCrop = [706, 71, 456, 600];
const flight = document.getElementById("flight-frame");
const trajectory = document.getElementById("trajectory-frame");
const flightContext = flight.getContext("2d");
const routeCaption = document.getElementById("route-caption");
const replayPhase = document.getElementById("replay-phase");
const replayCoverage = document.getElementById("replay-coverage");
const replayCount = document.getElementById("replay-count");
const replayObservations = document.getElementById("replay-observations");
const replayProgress = document.getElementById("replay-progress");
replayProgress.max = referencePoints;
const rvizFrame = document.getElementById("rviz-frame");
const updateCloudCoverage = globalThis.rvizCloud && createRvizCloud(globalThis.rvizCloud, recordedRun);
rvizFrame.hidden = Boolean(updateCloudCoverage);
const cameraFrame = document.getElementById("camera-frame");
const cameraPlaceholder = document.getElementById("camera-placeholder");
const cameraInfo = document.getElementById("camera-info");
const replayGain = document.getElementById("replay-gain");
const replayViewpoint = document.getElementById("replay-viewpoint");
const playButton = document.getElementById("replay-play");
const position = document.getElementById("replay-position");
const timeLabel = document.getElementById("replay-time");
const replayError = document.getElementById("replay-error");

function bindReplaySwitch(id, select) {
  const buttons = [...document.getElementById(id).querySelectorAll("button")];
  for (const button of buttons) {
    button.addEventListener("click", () => {
      for (const choice of buttons) choice.setAttribute("aria-pressed", String(choice === button));
      select(button.dataset.view);
    });
  }
}

bindReplaySwitch("media-switch", view => {
  document.getElementById("flight-view").hidden = view !== "flight";
  document.getElementById("camera-view").hidden = view !== "camera";
});
bindReplaySwitch("mobile-switch", view => {
  document.getElementById("replay").dataset.mobileView = view;
});
const observationCount = captureTimes.length;
document.getElementById("viewpoint-count").textContent = observationCount + " 个";
const finalPoints = coveredPoints.at(-1).toLocaleString("zh-CN") + " / " + referenceLabel;
document.getElementById("summary-coverage").textContent = percentLabel(observationCount);
document.getElementById("summary-points").textContent = finalPoints + " 点";
document.getElementById("summary-remaining").textContent = (referencePoints - coveredPoints.at(-1)).toLocaleString("zh-CN");
const seconds = Math.round(recordedRun.elapsedSeconds);
document.getElementById("summary-time").textContent = Math.floor(seconds / 60) + " 分 " + seconds % 60 + " 秒";
document.getElementById("trend-summary").textContent = observationCount + " 次有效观测共覆盖 " + finalPoints + " 个参考表面点。";
document.getElementById("replay-length").textContent = Math.round(recordedRun.videoDuration) + " 秒";
position.max = recordedRun.videoDuration;
const durationLabel = seconds => Math.floor(seconds / 60) + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");

const captureButtons = captureTimes.map((time, index) => {
  const button = document.createElement("button");
  button.className = "tab";
  button.type = "button";
  const name = document.createElement("strong");
  name.textContent = "视点 " + (index + 1);
  const detail = document.createElement("span");
  detail.textContent = "#" + viewpointIds[index] + " · " + durationLabel(time);
  button.append(name, detail);
  button.addEventListener("click", () => { video.currentTime = time; });
  document.getElementById("replay-views").append(button);
  return button;
});

function drawVideoRegion(ctx, crop, source = video) {
  const box = ctx.canvas.getBoundingClientRect();
  if (box.width && box.height) { const d = window.devicePixelRatio || 1; const w=Math.round(box.width*d), h=Math.round(box.height*d); if(ctx.canvas.width!==w || ctx.canvas.height!==h){ctx.canvas.width=w;ctx.canvas.height=h;} }
  const { width, height } = ctx.canvas;
  const scale = Math.max(width / crop[2], height / crop[3]);
  const frameWidth = crop[2] * scale;
  const frameHeight = crop[3] * scale;
  ctx.clearRect(0, 0, width, height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, ...crop, (width - frameWidth) / 2, (height - frameHeight) / 2, frameWidth, frameHeight);
}

const cameraPositions = recordedRun.cameraPositions;
const [vehicleX, vehicleY] = recordedRun.vehicleCenter;
const [vehicleLength, vehicleWidth] = recordedRun.vehicleDimensions;
const xBounds = [...cameraPositions.map(point => point[0]), vehicleX - vehicleLength / 2, vehicleX + vehicleLength / 2];
const yBounds = [...cameraPositions.map(point => point[1]), vehicleY - vehicleWidth / 2, vehicleY + vehicleWidth / 2];
const xMin = Math.min(...xBounds), xMax = Math.max(...xBounds);
const yMin = Math.min(...yBounds), yMax = Math.max(...yBounds);
const routeSize = new WeakMap();

function routeProjection(canvas) {
  const { width, height, density } = routeSize.get(canvas) ?? { width: canvas.width, height: canvas.height, density: 1 };
  const scale = Math.max(1, Math.min((width - 54) / Math.max(1, xMax - xMin), (height - 60) / Math.max(1, yMax - yMin)));
  const routeX = x => width / 2 + (x - (xMin + xMax) / 2) * scale;
  const routeY = y => height / 2 - (y - (yMin + yMax) / 2) * scale;
  return { width, height, density, scale, routeX, routeY, points: cameraPositions.map(([x, y]) => [routeX(x), routeY(y)]) };
}

function drawRoute(canvas, observed) {
  const ctx = canvas.getContext("2d");
  const { width, height, density, scale, routeX, routeY, points: routePoints } = routeProjection(canvas);
  const plotLeft = 21, plotRight = width - 12, plotTop = 21, plotBottom = height - 44;
  ctx.setTransform(density, 0, 0, density, 0, 0);
  ctx.fillStyle = "#f3faff";
  ctx.fillRect(0, 0, width, height);
  ctx.font = "12px system-ui";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let x = Math.floor(xMin) - 10; x <= Math.ceil(xMax) + 10; x++) {
    const px = routeX(x);
    if (px < plotLeft || px > plotRight) continue;
    ctx.fillStyle = "#d4e7ef";
    ctx.fillRect(px, plotTop, 1, plotBottom - plotTop);
    ctx.fillStyle = "#526980";
    if (x % 2 === 0) ctx.fillText(x, px, plotBottom + 12);
  }
  for (let y = Math.floor(yMin) - 10; y <= Math.ceil(yMax) + 10; y++) {
    const py = routeY(y);
    if (py < plotTop || py > plotBottom) continue;
    ctx.fillStyle = "#d4e7ef";
    ctx.fillRect(plotLeft, py, plotRight - plotLeft, 1);
    ctx.fillStyle = "#526980";
    if (y % 2 === 0) ctx.fillText(y, 12, py);
  }
  ctx.textAlign = "right";
  ctx.fillText("X (m)", width - 12, height - 7);
  ctx.textAlign = "center";
  ctx.fillText("Y (m)", 24, 9);
  ctx.beginPath();
  ctx.roundRect(routeX(vehicleX - vehicleLength / 2), routeY(vehicleY + vehicleWidth / 2), vehicleLength * scale, vehicleWidth * scale, 8);
  ctx.fillStyle = "#dbeafe";
  ctx.fill();
  ctx.strokeStyle = "#87afd0";
  ctx.stroke();
  ctx.fillStyle = "#244766";
  ctx.fillText("SUV", routeX(vehicleX), routeY(vehicleY));

  for (const [count, color, dash] of [[routePoints.length, "#7896ad", [5, 5]], [observed, "#087eaa", []]]) {
    if (!count) continue;
    ctx.beginPath();
    ctx.moveTo(...routePoints[0]);
    routePoints.slice(1, count).forEach(point => ctx.lineTo(...point));
    ctx.strokeStyle = color;
    ctx.lineWidth = dash.length ? 2 : 3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.setLineDash(dash);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  routePoints.forEach(([x, y], index) => {
    const active = index + 1 === observed;
    const seen = index < observed;
    ctx.beginPath();
    ctx.arc(x, y, active ? 13 : 10, 0, Math.PI * 2);
    ctx.fillStyle = active ? "#facc15" : seen ? "#087eaa" : "#f8fafc";
    ctx.fill();
    ctx.strokeStyle = active ? "#8a5508" : seen ? "#fff" : "#7896ad";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.font = "700 13px system-ui";
    ctx.fillStyle = active ? "#422006" : seen ? "#fff" : "#334e67";
    ctx.fillText(index + 1, x, y);
  });
}

const analysisRoute = document.getElementById("analysis-route");
const routeObserver = new ResizeObserver(entries => {
  for (const { target, contentRect } of entries) {
    if (!contentRect.width || !contentRect.height) continue;
    const { width, height } = contentRect;
    const density = Math.max(2, window.devicePixelRatio || 1);
    routeSize.set(target, { width, height, density });
    target.width = Math.round(width * density);
    target.height = Math.round(height * density);
    drawRoute(target, target === trajectory ? Math.max(0, shownObservation) : observationCount);
  }
});
routeObserver.observe(trajectory);
routeObserver.observe(analysisRoute);
drawRoute(analysisRoute, observationCount);
trajectory.addEventListener("click", event => {
  const box = trajectory.getBoundingClientRect();
  const { width, height, points } = routeProjection(trajectory);
  const x = (event.clientX - box.left) * width / box.width;
  const y = (event.clientY - box.top) * height / box.height;
  const distances = points.map(([px, py]) => Math.hypot(px - x, py - y));
  const nearest = Math.min(...distances);
  if (nearest <= 17) video.currentTime = captureTimes[distances.indexOf(nearest)];
});

let shownObservation = -1;
function updateReplay() {
  const time = video.currentTime;
  const observations = captureTimes.filter(capturedAt => time + 0.001 >= capturedAt).length;
  if (video.readyState >= 2) {
    syncFlight();
    if (flightVideo.readyState >= 2) drawVideoRegion(flightContext, [3,0,485,640], flightVideo);
    else drawVideoRegion(flightContext, flightCrop);
    if (recordedRun.cameraVideo) {
      drawVideoRegion(cameraVideoContext, cameraCrop);
      drawVideoRegion(cameraInsetContext, cameraCrop);
      cameraInsetContext.canvas.parentElement.hidden = false;
    }
  }
  if (shownObservation !== observations) {
    showObservation(observations);
    shownObservation = observations;
  }
  if (updateCloudCoverage) updateCloudCoverage(observations,time);
  position.value = time;
  timeLabel.textContent = durationLabel(time) + " / " + durationLabel(recordedRun.videoDuration);
}

function showObservation(observations) {
  const covered = coveredPoints[observations];
  const viewpoint = viewpointIds[observations - 1];
  drawRoute(trajectory, observations);
  routeCaption.textContent = observations
    ? "视点 " + viewpoint + " · 相机高度 " + recordedRun.cameraPositions[observations - 1][2].toFixed(2) + " m"
    : "实线为已回放，点击编号可跳转";
  if (updateCloudCoverage) updateCloudCoverage(observations);
  else rvizFrame.src = "assets/observations/rviz-" + observations + ".png";
  if (!recordedRun.cameraVideo && observations) cameraFrame.src = "assets/observations/camera-" + observations + ".jpg";
  cameraFrame.hidden = recordedRun.cameraVideo || !observations;
  cameraPlaceholder.hidden = Boolean(recordedRun.cameraVideo || observations);
  cameraInfo.textContent = recordedRun.cameraVideo ? "D435i · 同步录像" : observations ? "视点 " + viewpoint + " · 第 " + observations + " 次有效拍摄" : "等待首个视点拍摄";
  replayPhase.textContent = observations ? "第 " + observations + " 次有效观测 · 视点 " + viewpoint : "巡检起点";
  replayCoverage.textContent = percentLabel(observations);
  replayCount.textContent = covered.toLocaleString("zh-CN") + " / " + referenceLabel + " 点";
  replayObservations.textContent = observations + " / " + captureTimes.length;
  replayViewpoint.textContent = viewpoint ?? "—";
  replayGain.textContent = observations ? "+" + (covered - coveredPoints[observations - 1]).toLocaleString("zh-CN") + " 点" : "—";
  replayProgress.value = covered;
  captureButtons.forEach((button, index) => {
    if (index + 1 === observations) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
}

let frameRequest;
function animateReplay() {
  updateReplay();
  if (!video.paused && !video.ended) frameRequest = requestAnimationFrame(animateReplay);
}

video.addEventListener("loadeddata", updateReplay);
video.addEventListener("seeked", updateReplay);
video.addEventListener("play", () => {
  playButton.textContent = "Ⅱ";
  playButton.title = "暂停视频";
  playButton.setAttribute("aria-label", playButton.title);
  cancelAnimationFrame(frameRequest);
  animateReplay();
});
function stopAnimation() {
  cancelAnimationFrame(frameRequest);
  playButton.textContent = "▶";
  playButton.title = video.ended ? "重播视频" : "播放视频";
  playButton.setAttribute("aria-label", playButton.title);
  updateReplay();
}
video.addEventListener("pause", stopAnimation);
video.addEventListener("ended", stopAnimation);
video.addEventListener("error", () => { replayError.textContent = "视频加载失败，请确认素材文件可用。"; });
playButton.addEventListener("click", () => {
  if (!video.paused) {
    video.pause();
    return;
  }
  if (video.ended) video.currentTime = 0;
  video.play().catch(() => { replayError.textContent = "无法播放，请重试或下载原视频。"; });
});
position.addEventListener("input", () => { video.currentTime = Number(position.value); });
document.getElementById("replay-restart").addEventListener("click", () => {
  video.currentTime = 0;
  video.play().catch(() => { replayError.textContent = "无法重新播放。"; });
});

document.getElementById("playback-rate").addEventListener("input", event => {
  video.playbackRate = Number(event.target.value);

});
updateReplay();

function showView() {
  const hash = location.hash === "#diagnostics" ? "comparison" : location.hash.slice(1);
  const observation = Number(/^observation-(\d+)$/.exec(hash)?.[1]);
  const captureTime = captureTimes[observation - 1];
  const viewId = hash === "replay" || hash === "comparison" || captureTime !== undefined ? "workbench" : views.some(view => view.id === hash) ? hash : "home";
  home.hidden = viewId !== "home";
  vehicleModule.hidden = viewId === "home";
  document.getElementById("module-link").hidden = viewId === "home";
  views.forEach(view => { view.hidden = view.id !== viewId; });
  for (const link of links) {
    if (link.hash === "#" + viewId || (link.id === "module-link" && viewId !== "home")) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const analysis = document.getElementById('comparison');
  if (hash === 'comparison') {
    video.pause();
    if (!analysis.open) analysis.showModal();
  } else if (analysis.open) analysis.close();
  if (viewId !== "workbench") video.pause();
  if (captureTime !== undefined) video.currentTime = captureTime;
  if (hash === "replay" || captureTime !== undefined) document.getElementById("replay").scrollIntoView();
  else window.scrollTo(0, 0);
}

window.addEventListener("hashchange", showView);
showView();
document.getElementById('cloud-reset').addEventListener('click', () => {
  video.pause(); video.currentTime=0; shownObservation=-1; updateReplay();
  video.play().catch(()=>{ replayError.textContent='请点击播放继续。'; });
});
const inset=document.getElementById('camera-inset'), handle=inset.querySelector('figcaption');
let drag;
function constrainInset(){
  const parent=inset.parentElement;
  inset.style.maxWidth=parent.clientWidth+'px'; inset.style.maxHeight=parent.clientHeight+'px';
  if(inset.style.top){inset.style.left=Math.max(0,Math.min(parseFloat(inset.style.left)||0,parent.clientWidth-inset.offsetWidth))+'px'; inset.style.top=Math.max(0,Math.min(parseFloat(inset.style.top)||0,parent.clientHeight-inset.offsetHeight))+'px';}
}
handle.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const b=inset.getBoundingClientRect(),p=inset.parentElement.getBoundingClientRect();drag=[e.clientX,e.clientY,b.left-p.left,b.top-p.top];inset.style.left=drag[2]+'px';inset.style.top=drag[3]+'px';inset.style.bottom='auto';handle.setPointerCapture(e.pointerId);});
handle.addEventListener('pointermove',e=>{if(!drag)return;inset.style.left=drag[2]+e.clientX-drag[0]+'px';inset.style.top=drag[3]+e.clientY-drag[1]+'px';constrainInset();});
handle.addEventListener('lostpointercapture',()=>{drag=null;});
new ResizeObserver(()=>{constrainInset();updateReplay();}).observe(inset.parentElement);
new ResizeObserver(constrainInset).observe(inset);
const timingCanvas=document.getElementById('timing-trend');
new ResizeObserver(()=>{
 const w=timingCanvas.clientWidth,h=timingCanvas.clientHeight;if(!w||!h||!timings.length)return;
 const d=window.devicePixelRatio||1;timingCanvas.width=w*d;timingCanvas.height=h*d;
 const c=timingCanvas.getContext('2d');c.scale(d,d);
 const series=[{color:'#ea580c',v:timings.map(t=>t.seconds.reduce((a,b)=>a+b,0))},{color:'#0891b2',v:timings.map(t=>t.seconds[0])},{color:'#db2777',v:timings.map(t=>t.seconds[1])}];
 const max=Math.ceil(Math.max(...series.flatMap(s=>s.v))/40)*40,x=i=>48+(w-72)*i/Math.max(1,timings.length-1),y=v=>h-34-(h-58)*v/max;
 c.font='12px system-ui';for(let i=0;i<=4;i++){const v=max*i/4;c.strokeStyle='#e2e8f0';c.beginPath();c.moveTo(48,y(v));c.lineTo(w-24,y(v));c.stroke();c.fillStyle='#536680';c.textAlign='right';c.fillText(v+' s',41,y(v)+4);}
 c.textAlign='center';timings.forEach((t,i)=>c.fillText(i+1,x(i),h-12));
 for(const s of series){c.strokeStyle=s.color;c.fillStyle=s.color;c.lineWidth=2.5;c.beginPath();s.v.forEach((v,i)=>i?c.lineTo(x(i),y(v)):c.moveTo(x(i),y(v)));c.stroke();s.v.forEach((v,i)=>{c.beginPath();c.arc(x(i),y(v),3.5,0,Math.PI*2);c.fill();});}
}).observe(timingCanvas);

const roundDurations = timings.map(t => t.seconds.reduce((sum, v) => sum + v, 0));
if (roundDurations.length) {
  document.getElementById('timing-average').textContent = (roundDurations.reduce((a,b)=>a+b,0)/roundDurations.length).toFixed(1)+' s';
  document.getElementById('timing-longest').textContent = Math.max(...roundDurations).toFixed(1)+' s';
  document.getElementById('timing-views').textContent = captureTimes.length+' 个';
}
// Four-corner resizing with the opposite corner held in place.
function addCornerResize(box, floating) {
  box.classList.add('corner-resizable');
  for (const corner of ['nw', 'ne', 'sw', 'se']) {
    const grip = document.createElement('span');
    grip.className = 'resize-corner ' + corner;
    grip.setAttribute('role', 'separator');
    grip.setAttribute('aria-label', ({nw:'左上', ne:'右上', sw:'左下', se:'右下'})[corner] + '角调整窗口大小');
    box.append(grip);
    let origin;
    grip.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      const rect = box.getBoundingClientRect(), parent = box.parentElement.getBoundingClientRect();
      origin = { x:event.clientX, y:event.clientY, width:rect.width, height:rect.height,
        left:rect.left-parent.left-box.parentElement.clientLeft, top:rect.top-parent.top-box.parentElement.clientTop,
        shift:parseFloat(box.style.top)||0, margin:parseFloat(box.style.marginLeft)||0 };
      if (floating) { box.style.left=origin.left+'px'; box.style.top=origin.top+'px'; box.style.bottom='auto'; }
      grip.setPointerCapture(event.pointerId);
    });
    grip.addEventListener('pointermove', event => {
      if (!origin) return;
      const west=corner.includes('w'), north=corner.includes('n');
      const dx=event.clientX-origin.x, dy=event.clientY-origin.y;
      const parent=box.parentElement;
      const minWidth=floating?110:140, minHeight=floating?110:240;
      const availableWidth=west?origin.left+origin.width:parent.clientWidth-origin.left;
      const availableHeight=floating?(north?origin.top+origin.height:parent.clientHeight-origin.top):Math.max(origin.height,720);
      const width=Math.max(Math.min(minWidth,availableWidth),Math.min(availableWidth,origin.width+(west?-dx:dx)));
      const height=Math.max(Math.min(minHeight,availableHeight),Math.min(availableHeight,origin.height+(north?-dy:dy)));
      box.style.width=width+'px'; box.style.height=height+'px';
      if(floating){
        box.style.left=(origin.left+(west?origin.width-width:0))+'px';
        box.style.top=(origin.top+(north?origin.height-height:0))+'px';
      } else {
        box.style.marginLeft=(origin.margin+(west?origin.width-width:0))+'px';
        box.style.top=(origin.shift+(north?origin.height-height:0))+'px';
      }
    });
    grip.addEventListener('lostpointercapture', () => { origin=null; });
  }
}
addCornerResize(document.getElementById('cloud-view'), false);
addCornerResize(document.getElementById('camera-inset'), true);



const analysisDialog = document.getElementById('comparison');
document.getElementById('analysis-close').addEventListener('click', () => analysisDialog.close());
analysisDialog.addEventListener('close', () => {
  if (['#comparison','#diagnostics'].includes(location.hash)) location.hash = 'workbench';
});
