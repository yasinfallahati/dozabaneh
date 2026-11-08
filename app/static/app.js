const $ = (id) => document.getElementById(id);

const video = $("video");
const STAGES = {
  uploading: "بارگذاری ویدیو",
  queued: "در صف پردازش",
  preparing: "آماده‌سازی ویدیو برای پخش",
  listening: "شنیدن گفتار ویدیو",
  translating: "ترجمه‌ی زیرنویس‌ها",
};
const POPULAR = ["fa", "en", "ar", "tr", "fr", "de", "es", "ru", "zh", "ku", "ur", "hi"];
const WELCOME = ["ویدیو را اینجا رها کنید یا کلیک کنید", "Drop your video here, or click to choose"];
const NETWORK_ERROR = "ارتباط با برنامه قطع شد. مطمئن شوید برنامه در حال اجراست.";

const displayNames = new Intl.DisplayNames(["fa"], { type: "language" });
const percent = new Intl.NumberFormat("fa", { style: "percent" });
const digits = new Intl.NumberFormat("fa", { minimumIntegerDigits: 2, useGrouping: false });

let chosenFile = null;
let objectUrl = null;
let job = null;
let cues = [];
let activeIndex = -1;
let saveTimer = null;
let savePromise = Promise.resolve();

const languageName = (code) => {
  try {
    return displayNames.of(code);
  } catch {
    return code;
  }
};

const setState = (state) => { document.body.dataset.state = state; };

function showSubs(first, second = "") {
  $("subFirst").textContent = first;
  $("subSecond").textContent = second;
}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(NETWORK_ERROR);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `خطای ${response.status}`);
  }
  return response.json();
}

function fillSelect(select, codes, leading) {
  const byName = (a, b) => languageName(a).localeCompare(languageName(b), "fa");
  const popular = POPULAR.filter((code) => codes.includes(code));
  const others = codes.filter((code) => !popular.includes(code)).sort(byName);
  const option = (code, label) => new Option(label ?? languageName(code), code);
  const group = (label, items) => {
    const element = document.createElement("optgroup");
    element.label = label;
    element.append(...items.map((code) => option(code)));
    return element;
  };
  if (leading) select.append(option(leading[0], leading[1]));
  select.append(group("پرکاربرد", popular), group("همه‌ی زبان‌ها", others));
}

function syncLanguageOptions() {
  const first = $("lang1").value;
  for (const item of $("lang2").options) item.disabled = item.value === first;
  if ($("lang2").value === first) $("lang2").value = "";
}

/* Choosing a file */

function chooseFile(file) {
  if (!file) return;
  chosenFile = file;
  URL.revokeObjectURL(objectUrl);
  objectUrl = URL.createObjectURL(file);
  video.removeAttribute("controls");
  video.src = objectUrl;
  $("setupHint").textContent = "";
  $("screen").style.setProperty("--ratio", "16 / 9");
  setState("chosen");
  showSubs(file.name);
}

function resetToStart() {
  chosenFile = null;
  job = null;
  cues = [];
  video.removeAttribute("controls");
  video.removeAttribute("src");
  video.load();
  $("file").value = "";
  $("setupHint").textContent = "";
  $("screen").style.setProperty("--ratio", "16 / 9");
  history.replaceState(null, "", location.pathname);
  setState("empty");
  showSubs(...WELCOME);
}

/* Processing */

function showProgress(stage, progress) {
  setState("working");
  showSubs(STAGES[stage], percent.format(progress));
  $("trackFill").style.width = `${progress * 100}%`;
}

function showFailure(message) {
  setState("failed");
  showSubs(message, "برای انتخاب ویدیوی دیگر کلیک کنید");
}

function upload() {
  const params = new URLSearchParams({
    name: chosenFile.name,
    speech: $("speech").value,
    lang1: $("lang1").value,
    lang2: $("lang2").value,
    quality: $("quality").value,
  });
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/jobs?${params}`);
    request.responseType = "json";
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) showProgress("uploading", event.loaded / event.total);
    };
    request.onerror = () => reject(new Error(NETWORK_ERROR));
    request.onload = () => {
      if (request.status === 200) resolve(request.response);
      else reject(new Error(request.response?.detail ?? `خطای ${request.status}`));
    };
    request.send(chosenFile);
  });
}

async function followJob(jobId) {
  for (;;) {
    const current = await api(`/api/jobs/${jobId}`);
    if (current.status === "ready") return openEditor(current);
    if (current.status === "failed") return showFailure(current.error);
    showProgress(current.status, current.progress);
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
}

async function startJob(event) {
  event.preventDefault();
  if (!chosenFile) {
    $("setupHint").textContent = "اول یک ویدیو انتخاب کنید.";
    return;
  }
  try {
    showProgress("uploading", 0);
    const created = await upload();
    history.replaceState(null, "", `#${created.id}`);
    await followJob(created.id);
  } catch (error) {
    setState("chosen");
    showSubs(chosenFile.name);
    $("setupHint").textContent = error.message;
  }
}

/* Editing */

function formatTime(seconds) {
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const parts = [Math.floor(whole / 60) % 60, whole % 60];
  if (hours) parts.unshift(hours);
  return parts.map((part) => digits.format(part)).join(":");
}

function languages() {
  return [job.lang1, job.lang2].filter(Boolean);
}

function buildCueRow(cue, index) {
  const row = document.createElement("div");
  row.className = "cue";
  const time = document.createElement("button");
  time.type = "button";
  time.className = "time";
  time.textContent = formatTime(cue.start);
  time.addEventListener("click", () => seekTo(cue));
  const lines = document.createElement("div");
  lines.className = "lines";
  languages().forEach((lang, position) => {
    const area = document.createElement("textarea");
    area.rows = 1;
    area.dir = "auto";
    area.value = cue.texts[lang];
    area.className = position ? "second" : "first";
    area.setAttribute("aria-label", `زیرنویس ${languageName(lang)}، ردیف ${index + 1}`);
    area.addEventListener("input", () => {
      cue.texts[lang] = area.value;
      activeIndex = -1;
      scheduleSave();
    });
    area.addEventListener("focus", () => { if (video.paused) seekTo(cue, false); });
    lines.append(area);
  });
  row.append(time, lines);
  return row;
}

function seekTo(cue, play = true) {
  video.currentTime = cue.start + 0.01;
  if (play) video.play().catch(() => {});
}

function openEditor(current) {
  job = current;
  return api(`/api/jobs/${job.id}/cues`).then((loaded) => {
    cues = loaded;
    $("screen").style.setProperty("--ratio", `${job.width} / ${job.height}`);
    video.src = `/api/jobs/${job.id}/video`;
    video.controls = true;
    showSubs("");
    setState("ready");

    $("legend").replaceChildren(
      ...languages().map((lang, position) => {
        const label = document.createElement("span");
        const swatch = document.createElement("i");
        swatch.className = `swatch ${position ? "two" : "one"}`;
        label.append(swatch, languageName(lang));
        return label;
      }),
    );
    $("cues").replaceChildren(...cues.map(buildCueRow));
    activeIndex = -1;

    $("srt1").href = `/api/jobs/${job.id}/srt/${job.lang1}`;
    $("srt1").textContent = `دانلود فایل زیرنویس ${languageName(job.lang1)}`;
    $("srt2").hidden = !job.lang2;
    if (job.lang2) {
      $("srt2").href = `/api/jobs/${job.id}/srt/${job.lang2}`;
      $("srt2").textContent = `دانلود فایل زیرنویس ${languageName(job.lang2)}`;
    }
    showRender(job.render);
    if (job.render.status === "running") followRender();
  });
}

function scheduleSave() {
  $("saved").textContent = "در حال ذخیره…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 500);
}

function saveNow() {
  clearTimeout(saveTimer);
  saveTimer = null;
  savePromise = savePromise
    .then(() => api(`/api/jobs/${job.id}/cues`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cues.map((cue) => ({ texts: cue.texts }))),
    }))
    .then(() => {
      $("saved").textContent = "ذخیره شد";
      showRender({ status: "none" });
    })
    .catch((error) => { $("saved").textContent = `ذخیره نشد: ${error.message}`; });
  return savePromise;
}

function paintSubtitles() {
  if (document.body.dataset.state === "ready" && cues.length) {
    const time = video.currentTime;
    const index = cues.findIndex((cue) => time >= cue.start && time < cue.end);
    if (index !== activeIndex) {
      const rows = $("cues").children;
      rows[activeIndex]?.classList.remove("active");
      rows[index]?.classList.add("active");
      activeIndex = index;
      const cue = cues[index];
      showSubs(cue?.texts[job.lang1].trim() ?? "", job.lang2 ? cue?.texts[job.lang2].trim() ?? "" : "");
      const editing = document.activeElement?.tagName === "TEXTAREA";
      if (cue && !editing && !video.paused && matchMedia("(min-width: 1000px)").matches) {
        rows[index].scrollIntoView({ block: "nearest" });
      }
    }
  }
  requestAnimationFrame(paintSubtitles);
}

/* Burning subtitles into the video */

function showRender(render) {
  const button = $("render");
  const link = $("downloadVideo");
  const done = render.status === "done";
  const running = render.status === "running";
  button.hidden = done;
  link.hidden = !done;
  link.href = done ? `/api/jobs/${job.id}/output` : "";
  button.disabled = running;
  button.textContent = running
    ? `ساخت ویدیو، ${percent.format(render.progress)}`
    : "ساخت ویدیو با زیرنویس";
  $("cues").disabled = running;
  $("renderHint").textContent = render.status === "failed" ? render.error : "";
}

async function followRender() {
  try {
    for (;;) {
      const { render } = await api(`/api/jobs/${job.id}`);
      showRender(render);
      if (render.status !== "running") return;
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
  } catch (error) {
    $("renderHint").textContent = error.message;
  }
}

async function startRender() {
  try {
    if (saveTimer !== null) saveNow();
    await savePromise;
    const { render } = await api(`/api/jobs/${job.id}/render`, { method: "POST" });
    showRender(render);
    followRender();
  } catch (error) {
    $("renderHint").textContent = error.message;
  }
}

/* Prerequisites */

const sizeNumber = new Intl.NumberFormat("fa", { maximumFractionDigits: 1 });
const formatSize = (megabytes) =>
  megabytes >= 1000
    ? `${sizeNumber.format(megabytes / 1000)} گیگابایت`
    : `${sizeNumber.format(megabytes)} مگابایت`;

function setupRow(item) {
  const row = document.createElement("li");
  const name = document.createElement("span");
  name.textContent = item.label;
  if (item.size_mb && !item.installed) {
    const size = document.createElement("small");
    size.textContent = formatSize(item.size_mb);
    name.append(size);
  }
  const status = document.createElement("span");
  status.className = "prereq-status";
  let tone = "";
  if (item.state === "failed") {
    status.textContent = item.error;
    tone = "bad";
  } else if (item.installed) {
    status.textContent = "نصب است";
    tone = "ok";
  } else if (item.state === "installing") {
    status.textContent = percent.format(item.progress);
    tone = "busy";
  } else if (item.state === "waiting") {
    status.textContent = "در صف دانلود";
  } else {
    status.textContent = item.command ? "نیاز به نصب دستی" : "نصب نیست";
    if (item.command) tone = "bad";
  }
  row.className = `prereq-row ${tone}`;
  row.append(name, status);
  if (item.command && !item.installed) {
    const command = document.createElement("code");
    command.dir = "ltr";
    command.textContent = item.command;
    row.append(command);
  }
  return row;
}

function overallProgress(items) {
  const sized = items.filter((item) => item.size_mb && item.state !== "idle");
  const total = sized.reduce((sum, item) => sum + item.size_mb, 0);
  const done = sized.reduce(
    (sum, item) => sum + item.size_mb * (item.installed ? 1 : item.progress),
    0,
  );
  return total ? done / total : 0;
}

function showSetup({ status, items }) {
  const button = $("setupButton");
  const labels = {
    missing: "نصب پیش‌نیازها",
    failed: "تلاش دوباره برای نصب",
    running: `نصب پیش‌نیازها، ${percent.format(overallProgress(items))}`,
    manual: "یک مورد نصب دستی لازم دارد",
    complete: "پیش‌نیازها نصب است",
  };
  button.textContent = labels[status];
  button.className = ["missing", "failed", "running"].includes(status) ? "primary" : "quiet";
  button.disabled = status === "running";
  button.dataset.status = status;

  const missing = items
    .filter((item) => !item.installed && item.size_mb)
    .reduce((sum, item) => sum + item.size_mb, 0);
  $("prereqNote").textContent =
    missing && status !== "running" ? `حدود ${formatSize(missing)} دانلود می‌شود.` : "";
  $("prereqList").replaceChildren(...items.map(setupRow));
}

async function followSetup() {
  try {
    for (;;) {
      const state = await api("/api/setup");
      showSetup(state);
      if (state.status !== "running") return;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    $("prereqNote").textContent = error.message;
  }
}

async function pressSetup() {
  const { status } = $("setupButton").dataset;
  if (status === "missing" || status === "failed") {
    $("prereq").hidden = false;
    try {
      showSetup(await api("/api/setup", { method: "POST" }));
      followSetup();
    } catch (error) {
      $("prereqNote").textContent = error.message;
    }
  } else {
    $("prereq").hidden = !$("prereq").hidden;
  }
}

/* Wiring */

const canChoose = () => ["empty", "chosen", "failed"].includes(document.body.dataset.state);

$("file").addEventListener("change", (event) => chooseFile(event.target.files[0]));
$("setup").addEventListener("submit", startJob);
$("lang1").addEventListener("change", syncLanguageOptions);
$("render").addEventListener("click", startRender);
$("restart").addEventListener("click", resetToStart);
$("setupButton").addEventListener("click", pressSetup);

const screen = $("screen");
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
screen.addEventListener("dragover", () => { if (canChoose()) screen.classList.add("dragging"); });
screen.addEventListener("dragleave", () => screen.classList.remove("dragging"));
screen.addEventListener("drop", (event) => {
  screen.classList.remove("dragging");
  if (canChoose()) chooseFile(event.dataTransfer.files[0]);
});

async function start() {
  showSubs(...WELCOME);
  const available = await api("/api/languages");
  fillSelect($("speech"), available.speech, ["auto", "تشخیص خودکار"]);
  fillSelect($("lang1"), available.translate);
  fillSelect($("lang2"), available.translate, ["", "بدون زیرنویس دوم"]);
  $("lang1").value = "fa";
  $("lang2").value = "";
  syncLanguageOptions();
  requestAnimationFrame(paintSubtitles);
  followSetup();

  const jobId = location.hash.slice(1);
  if (jobId) await followJob(jobId).catch(resetToStart);
}

start().catch((error) => showSubs(error.message));
