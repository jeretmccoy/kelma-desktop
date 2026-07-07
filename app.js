const repo = "jeretmccoy/kelma-desktop";
const apiBase = `https://api.github.com/repos/${repo}`;
const runListUrl = `${apiBase}/actions/workflows/release.yml/runs?event=workflow_dispatch&status=completed&per_page=20`;

const platforms = [
  {
    key: "mac",
    title: "macOS",
    icon: "MAC",
    description: "Choose the build that matches your Mac processor.",
    artifacts: [
      { name: "installer-macos", label: "Apple Silicon" },
      { name: "installer-macos-intel", label: "Intel" },
    ],
  },
  {
    key: "windows",
    title: "Windows",
    icon: "WIN",
    description: "Installers for Windows PCs. ARM builds appear when available.",
    artifacts: [
      { name: "installer-windows", label: "Windows x64" },
      { name: "unsigned-installer-windows-arm", label: "Windows ARM" },
      { name: "installer-windows-arm", label: "Windows ARM" },
    ],
  },
  {
    key: "linux",
    title: "Linux",
    icon: "LIN",
    description: "Compressed desktop archives for Linux workstations.",
    artifacts: [
      { name: "installer-linux-x86", label: "Linux x86_64" },
      { name: "installer-linux-arm", label: "Linux ARM" },
    ],
  },
];

const grid = document.querySelector("#download-grid");
const template = document.querySelector("#download-card-template");
const statusDot = document.querySelector("#status-dot");
const statusTitle = document.querySelector("#status-title");
const statusDetail = document.querySelector("#status-detail");

function setStatus(kind, title, detail) {
  statusDot.className = `status-dot ${kind}`;
  statusTitle.textContent = title;
  statusDetail.textContent = detail;
}

function formatDate(dateText) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(dateText));
}

function detectPlatformKey() {
  const platform = navigator.userAgentData?.platform || navigator.platform || "";
  const text = platform.toLowerCase();

  if (text.includes("mac")) return "mac";
  if (text.includes("win")) return "windows";
  if (text.includes("linux")) return "linux";
  return "";
}

function artifactUrl(artifact, run) {
  return `https://github.com/${repo}/actions/runs/${run.id}/artifacts/${artifact.id}`;
}

function renderDownloads(artifacts, run) {
  const artifactMap = new Map(artifacts.map((artifact) => [artifact.name, artifact]));
  const detected = detectPlatformKey();
  grid.replaceChildren();

  for (const platform of platforms) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.querySelector(".platform-icon").textContent = platform.icon;
    card.querySelector("h2").textContent = platform.title;
    card.querySelector(".download-description").textContent = platform.description;

    const badge = card.querySelector(".recommended");
    if (platform.key === detected) {
      badge.hidden = false;
    }

    const actions = card.querySelector(".download-actions");
    const seenLabels = new Set();

    for (const candidate of platform.artifacts) {
      const artifact = artifactMap.get(candidate.name);
      if (!artifact || seenLabels.has(candidate.label)) {
        continue;
      }

      seenLabels.add(candidate.label);
      const link = document.createElement("a");
      link.className = actions.children.length ? "download-button secondary" : "download-button";
      link.href = artifactUrl(artifact, run);
      link.rel = "noopener";
      link.textContent = candidate.label;
      link.setAttribute("aria-label", `Download ${candidate.label} artifact ZIP`);
      actions.append(link);
    }

    const meta = document.createElement("p");
    meta.className = "download-meta";
    meta.textContent = actions.children.length
      ? `Build ${run.head_sha.slice(0, 7)} - expires ${formatDate(artifacts[0].expires_at)}`
      : "No current artifact for this platform.";
    actions.append(meta);
    grid.append(card);
  }
}

function renderFallback(title, detail, runUrl) {
  grid.replaceChildren();

  const card = document.createElement("article");
  card.className = "download-card";
  card.innerHTML = `
    <div class="platform-icon" aria-hidden="true">!</div>
    <div class="download-content">
      <div class="download-heading"><h2>${title}</h2></div>
      <p class="download-description">${detail}</p>
      <div class="download-actions">
        <a class="download-button" href="${runUrl}" rel="noopener">Open build history</a>
      </div>
    </div>
  `;
  grid.append(card);
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });

  if (!response.ok) {
    throw new Error(`GitHub API returned ${response.status}`);
  }

  return response.json();
}

async function loadDownloads() {
  try {
    const runs = await fetchJson(runListUrl);
    const run = runs.workflow_runs.find(
      (candidate) =>
        candidate.conclusion === "success" &&
        candidate.display_title.includes("sign=false") &&
        candidate.display_title.includes("draft=false") &&
        candidate.display_title.includes("testpypi=false") &&
        candidate.display_title.includes("pypi=false")
    );

    if (!run) {
      setStatus(
        "warn",
        "No successful unsigned build yet",
        "Start a build-only Release workflow run, then refresh this page."
      );
      renderFallback(
        "Downloads are not ready",
        "The page is deployed, but there is not a successful unsigned installer build to download yet.",
        `https://github.com/${repo}/actions/workflows/release.yml`
      );
      return;
    }

    const artifactsResponse = await fetchJson(`${apiBase}/actions/runs/${run.id}/artifacts?per_page=100`);
    const artifacts = artifactsResponse.artifacts.filter(
      (artifact) =>
        !artifact.expired &&
        (artifact.name.startsWith("installer-") || artifact.name.startsWith("unsigned-installer-"))
    );

    if (!artifacts.length) {
      setStatus("warn", "Build found without installer artifacts", `Latest run completed ${formatDate(run.updated_at)}.`);
      renderFallback(
        "Artifacts unavailable",
        "The latest successful run did not expose current installer artifacts.",
        run.html_url
      );
      return;
    }

    setStatus("ready", "Latest unsigned build ready", `Built from ${run.head_branch} on ${formatDate(run.updated_at)}.`);
    renderDownloads(artifacts, run);
  } catch (error) {
    setStatus("warn", "Could not load downloads", error.message);
    renderFallback(
      "Open build history",
      "GitHub could not be reached from this browser. The build history still has the artifacts.",
      `https://github.com/${repo}/actions/workflows/release.yml`
    );
  }
}

loadDownloads();
