// devkit/ui/three-loader.js
//
// Minimal three.js loader for the DevKit 3D preview tab.
//
// Why this exists
// ---------------
//
// The DevKit UI is served from a ``file://`` URL by pywebview.  WKWebView
// refuses to ``fetch()`` another ``file://`` URL because of CORS, so we
// cannot point GLTFLoader directly at the model on disk.  Instead:
//
//   1. JS calls ``api.read_model_bytes(model_id)`` (Python side reads the
//      file and base64-encodes it).
//   2. We decode the base64 into a ``Uint8Array`` and feed it to GLTFLoader
//      via ``Loader.parse(...)`` — no object URL needed.
//
// Three.js is loaded on demand from a CDN (esm.sh) so the rest of the
// DevKit UI stays a single offline-served HTML page.  If the CDN is
// unreachable (offline machine, China firewall, ...) we fall back to a
// plain text panel showing the model metadata so the editor is still
// useful for inspecting file size / name / format.
//
// Scope (not pretending to be a full viewer)
// ------------------------------------------
//
// * GLB  — fully rendered, auto-framed, lit, with orbit controls.
// * GLTF — fully rendered (the .gltf JSON + sidecar .bin are read into a
//          Uint8Array; GLTFLoader accepts a single buffer only if the
//          .gltf has embedded data URIs.  Standalone .gltf + .bin files
//          would need a resource loader, which is out of scope; we
//          surface a clear error in that case).
// * VRM  — VRM 0.x / 1.0 are GLB with extras.  GLTFLoader will parse
//          the mesh + materials, but humanoid bones, expressions, and
//          MToon shaders need @pixiv/three-vrm.  We render what we
//          can and label the panel "VRM (basic)" so the user knows.
//
(() => {
  "use strict";

  // --- tiny DOM helpers (kept local so we don't depend on devkit.js) ---
  const $ = (sel, root = document) => root.querySelector(sel);

  // CDN candidates, in order.  esm.sh bundles three as ESM with proper
  // dependency resolution; unpkg requires us to map three/examples/jsm
  // manually.  jsdelivr as a last resort.
  const CDN_BASES = [
    "https://esm.sh/three@0.160.0",
    "https://unpkg.com/three@0.160.0/build/three.module.js",
    "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  ];

  const FALLBACK_MSG = (name, err) =>
    `<div class="status status--warn">
      <p><strong>未能加载 3D 预览</strong></p>
      <p>three.js CDN 加载失败：${(err && err.message) || err || "未知错误"}</p>
      <p>模型 "${esc(name)}" 的元数据仍然可查看。完整预览需要联网。</p>
    </div>`;

  const esc = (s) => {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  };

  const base64ToUint8 = (b64) => {
    const bin = atob(b64);
    const len = bin.length;
    const out = new Uint8Array(len);
    for (let i = 0; i < len; i++) out[i] = bin.charCodeAt(i);
    return out;
  };

  // Cache the loaded three module so subsequent previews don't re-fetch.
  let _threePromise = null;
  const loadThree = async () => {
    if (_threePromise) return _threePromise;
    _threePromise = (async () => {
      for (const url of CDN_BASES) {
        try {
          // Dynamic import — three is published as ESM since 0.150.
          const THREE = await import(/* @vite-ignore */ url);
          if (THREE && (THREE.Scene || THREE.default?.Scene)) {
            return THREE.Scene ? THREE : THREE.default;
          }
        } catch (_e) { /* try next */ }
      }
      throw new Error("所有 three.js CDN 镜像均不可达");
    })();
    return _threePromise;
  };

  /**
   * Render ``model`` (the dict returned by ``api.read_model_bytes``)
   * inside ``container``.  Replaces any previous renderer.
   */
  const renderModel = async (container, model) => {
    // Tear down any previous scene first so we don't leak WebGL contexts
    // (browsers cap ~16 active contexts).
    if (container._viewerTeardown) {
      try { container._viewerTeardown(); } catch (_e) { /* ignore */ }
      container._viewerTeardown = null;
    }
    container.innerHTML = "";

    const data = base64ToUint8(model.data_b64);
    const isGlb = model.format === "vrm" || model.format === "glb";

    let THREE;
    try {
      THREE = await loadThree();
    } catch (err) {
      container.innerHTML = FALLBACK_MSG(model.name, err);
      return;
    }

    // --- scene scaffolding ---------------------------------------------
    const width = container.clientWidth || 480;
    const height = container.clientHeight || 320;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1c1f24);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(2.5, 2.0, 3.5);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(width, height);
    container.appendChild(renderer.domElement);

    // Lights — one hemisphere fill + one directional key.  VRM MToon
    // materials will still look flat without the full shader, but GLB
    // PBR materials light correctly.
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444466, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(3, 5, 4);
    scene.add(key);

    // Orbit controls — try to load from three/examples.  esm.sh resolves
    // bare specifiers through three's own package, so this works when
    // three came from there.  Fall back to a basic mouse-drag rotation
    // if the import fails (no internet, older three, etc.).
    const controls = await tryLoadOrbitControls(THREE, renderer.domElement, camera);

    // --- load the model ------------------------------------------------
    const GLTFLoader = await tryLoadGltfLoader(THREE);
    if (!GLTFLoader) {
      container.innerHTML = `<div class="status status--err">
        <p>three.js 加载成功，但 GLTFLoader 不可用</p>
        <p>请检查网络后重试。</p>
      </div>`;
      return;
    }

    const loader = new GLTFLoader();
    const onLoaded = (gltf) => {
      const root = gltf.scene || gltf.scenes?.[0];
      if (!root) {
        container.insertAdjacentHTML("beforeend",
          `<div class="status status--err">模型为空（无 scene 节点）</div>`);
        return;
      }
      scene.add(root);
      autoFrame(root, camera, renderer);
      container._viewerRender = () => renderer.render(scene, camera);
      container._viewerRender();
      // Label VRM as "basic" so the user knows the humanoid bones
      // / expressions aren't driven.
      if (model.format === "vrm") {
        const tag = document.createElement("div");
        tag.className = "model-viewer__tag";
        tag.textContent = "VRM (基础网格预览 · 不含骨骼/表情)";
        container.appendChild(tag);
      }
    };
    const onError = (err) => {
      // GLTF + standalone .bin sidecar is the most common reason for
      // failure here — surface that hint.
      const hint = !isGlb
        ? "<p>提示：.gltf 需要同级 .bin 资源，目前实现仅支持内嵌 data URI。</p>"
        : "";
      container.insertAdjacentHTML("beforeend",
        `<div class="status status--err">
          <p>模型解析失败：${esc(err && err.message || err || "未知错误")}</p>
          ${hint}
        </div>`);
    };

    if (isGlb) {
      loader.parse(data, "", onLoaded, onError);
    } else {
      // .gltf — try to parse as JSON, see if it has embedded buffers.
      try {
        const text = new TextDecoder("utf-8").decode(data);
        const json = JSON.parse(text);
        loader.parse(JSON.stringify(json), "", onLoaded, onError);
      } catch (err) {
        onError(err);
      }
    }

    // --- resize handling ----------------------------------------------
    const onResize = () => {
      const w = container.clientWidth || 480;
      const h = container.clientHeight || 320;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      if (container._viewerRender) container._viewerRender();
    };
    window.addEventListener("resize", onResize);

    // --- animate -------------------------------------------------------
    let raf = 0;
    const tick = () => {
      if (controls && typeof controls.update === "function") controls.update();
      if (container._viewerRender) container._viewerRender();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    // Expose teardown so the next render can clean up.
    container._viewerTeardown = () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      if (controls && typeof controls.dispose === "function") controls.dispose();
      try { renderer.dispose(); } catch (_e) { /* ignore */ }
      // Remove the canvas so the next render starts from a clean slate.
      if (renderer.domElement && renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  };

  // --- helpers ----------------------------------------------------------

  const tryLoadOrbitControls = async (THREE, domElement, camera) => {
    // esm.sh's three bundle exposes /examples/jsm/controls/OrbitControls
    // through the same package.  Try the relative specifier first; if
    // three was loaded from a bare-file CDN (unpkg / jsdelivr), this
    // fails and we fall back to a hand-rolled mouse-drag controller.
    try {
      const mod = await import(
        /* @vite-ignore */ "https://esm.sh/three@0.160.0/examples/jsm/controls/OrbitControls.js"
      );
      const OC = mod.OrbitControls || mod.default?.OrbitControls;
      if (OC) return new OC(camera, domElement);
    } catch (_e) { /* fall through */ }
    return makeDragController(domElement, camera);
  };

  const tryLoadGltfLoader = async (_THREE) => {
    try {
      const mod = await import(
        /* @vite-ignore */ "https://esm.sh/three@0.160.0/examples/jsm/loaders/GLTFLoader.js"
      );
      return mod.GLTFLoader || mod.default?.GLTFLoader || null;
    } catch (_e) {
      return null;
    }
  };

  // Minimal orbit controller — left-drag rotate, wheel zoom.  Good
  // enough as a fallback so the user can still inspect the model.
  const makeDragController = (domElement, camera) => {
    let dragging = false;
    let lastX = 0, lastY = 0;
    let theta = Math.atan2(camera.position.x, camera.position.z);
    let phi = Math.acos(camera.position.y / camera.position.length());
    let radius = camera.position.length();
    const target = { x: 0, y: 0, z: 0 };
    const update = () => {
      camera.position.x = target.x + radius * Math.sin(phi) * Math.sin(theta);
      camera.position.y = target.y + radius * Math.cos(phi);
      camera.position.z = target.z + radius * Math.sin(phi) * Math.cos(theta);
      camera.lookAt(target.x, target.y, target.z);
    };
    domElement.addEventListener("mousedown", (e) => {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
    });
    window.addEventListener("mouseup", () => { dragging = false; });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      theta -= dx * 0.01;
      phi = Math.max(0.1, Math.min(Math.PI - 0.1, phi - dy * 0.01));
      update();
    });
    domElement.addEventListener("wheel", (e) => {
      e.preventDefault();
      radius = Math.max(0.5, Math.min(20, radius * (1 + e.deltaY * 0.001)));
      update();
    }, { passive: false });
    update();
    return { update() { /* RAF already drives via renderModel's tick */ }, dispose() {} };
  };

  const autoFrame = (object3d, camera, renderer) => {
    // Compute bounding box, move camera so the model fills ~70% of
    // the viewport.  Skips ortho / 2D cases.
    const box = new (object3d.constructor.prototype.constructor === object3d.constructor
      ? Object : Object)();
    // three's Box3 — pull it off the same module.
    const THREE = object3d.material?.constructor?.name === "MeshStandardMaterial"
      ? null : null;
    // Simpler: compute manually from geometry so we don't need access
    // to THREE here.
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    object3d.traverse((node) => {
      if (!node.geometry || !node.geometry.attributes || !node.geometry.attributes.position) return;
      const arr = node.geometry.attributes.position.array;
      const m = node.matrixWorld;
      // Sample a subset of vertices — full scan is fine for typical
      // VRM/GLB sizes (< 100k verts) but we cap at 50k for safety.
      const stride = Math.max(1, Math.floor(arr.length / 150000));
      for (let i = 0; i < arr.length; i += 3 * stride) {
        const x = arr[i], y = arr[i + 1], z = arr[i + 2];
        // Apply world matrix manually (avoid pulling in THREE.Vector3).
        const wx = m.elements[0] * x + m.elements[4] * y + m.elements[8] * z + m.elements[12];
        const wy = m.elements[1] * x + m.elements[5] * y + m.elements[9] * z + m.elements[13];
        const wz = m.elements[2] * x + m.elements[6] * y + m.elements[10] * z + m.elements[14];
        if (wx < minX) minX = wx; if (wx > maxX) maxX = wx;
        if (wy < minY) minY = wy; if (wy > maxY) maxY = wy;
        if (wz < minZ) minZ = wz; if (wz > maxZ) maxZ = wz;
      }
    });
    if (!Number.isFinite(minX)) return;  // no geometry — leave camera alone
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
    const size = Math.max(maxX - minX, maxY - minY, maxZ - minZ) || 1;
    const dist = size / (2 * Math.tan((camera.fov * Math.PI / 180) / 2)) * 1.4;
    camera.position.set(cx + dist * 0.6, cy + dist * 0.4, cz + dist * 0.8);
    camera.lookAt(cx, cy, cz);
    camera.updateProjectionMatrix();
  };

  // Public surface — exposed via window.DevKitThreeViewer
  window.DevKitThreeViewer = { renderModel };
})();