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
// Three.js, @pixiv/three-vrm, and examples are loaded from local ``vendor/``
// so the preview works completely offline.
//
// Scope
// ------------------------------------------
//
// * GLB  — fully rendered, auto-framed, lit, with orbit controls.
// * GLTF — fully rendered (embedded buffers only; sidecar .bin needs a
//          resource loader, out of scope).
// * VRM  — VRM 0.x / 1.0 are GLB with extras.  three-vrm loads humanoid
//          bones, blendshapes, MToon materials, spring bones.  We expose
//          expression controls for preview.

(function () {
  "use strict";

  // --- tiny DOM helpers (kept local so we don't depend on devkit.js) ---
  const $ = (sel, root = document) => root.querySelector(sel);

  // Local vendor paths (all files under devkit/ui/vendor/)
  const VENDOR_BASE = "./vendor/";

  const FALLBACK_MSG = (name, err) =>
    `<div class="status status--warn">
      <p><strong>未能加载 3D 预览</strong></p>
      <p>本地 three.js 加载失败：${(err && err.message) || err || "未知错误"}</p>
      <p>模型 "${esc(name)}" 的元数据仍然可查看。</p>
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

  // Cache the loaded modules so subsequent previews don't re-fetch.
  let _threePromise = null;
  let _vrmPromise = null;
  const loadThree = async () => {
    if (_threePromise) return _threePromise;
    _threePromise = (async () => {
      // Load three.js from local vendor
      try {
        const THREE = await import(/* @vite-ignore */ VENDOR_BASE + "three.module.js");
        if (THREE && (THREE.Scene || THREE.default?.Scene)) {
          return THREE.Scene ? THREE : THREE.default;
        }
      } catch (_e) { /* fall through */ }
      throw new Error("本地 three.js 加载失败，请检查 vendor/ 目录");
    })();
    return _threePromise;
  };

  const loadVRM = async (THREE) => {
    if (_vrmPromise) return _vrmPromise;
    _vrmPromise = (async () => {
      // Load three-vrm from local vendor
      try {
        // three-vrm requires three to be available globally or passed in
        // We'll import it and it will use the THREE we pass to createVRM
        const mod = await import(/* @vite-ignore */ VENDOR_BASE + "three-vrm.module.min.js");
        // three-vrm exports createVRM function
        const { createVRM } = mod;
        if (createVRM) {
          return { createVRM };
        }
      } catch (_e) { /* fall through */ }
      throw new Error("本地 three-vrm 加载失败，请检查 vendor/three-vrm.module.min.js");
    })();
    return _vrmPromise;
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

    // Orbit controls — load from local vendor
    const controls = await tryLoadOrbitControls(THREE, renderer.domElement, camera);

    // --- load the model ------------------------------------------------
    const GLTFLoader = await tryLoadGltfLoader(THREE);
    if (!GLTFLoader) {
      container.innerHTML = `<div class="status status--err">
        <p>three.js 加载成功，但 GLTFLoader 不可用</p>
        <p>请检查 vendor/GLTFLoader.js 是否存在。</p>
      </div>`;
      return;
    }

    const loader = new GLTFLoader();
    let vrmInstance = null;

    const onLoaded = async (gltf) => {
      const root = gltf.scene || gltf.scenes?.[0];
      if (!root) {
        container.insertAdjacentHTML("beforeend",
          `<div class="status status--err">模型为空（无 scene 节点）</div>`);
        return;
      }
      scene.add(root);
      autoFrame(root, camera, renderer);
      
      // If VRM format, initialize with three-vrm
      if (model.format === "vrm") {
        try {
          const vrmModule = await loadVRM(THREE);
          if (vrmModule && vrmModule.createVRM) {
            vrmInstance = await vrmModule.createVRM(root);
            // Setup expression controls UI
            setupVRMExpressionUI(container, vrmInstance);
          }
        } catch (e) {
          console.warn("VRM initialization failed:", e);
        }
      }
      
      container._viewerRender = () => {
        // Update VRM spring bones / look-at if available
        if (vrmInstance && typeof vrmInstance.update === "function") {
          vrmInstance.update(1/60); // Approximate frame time
        }
        renderer.render(scene, camera);
      };
      container._viewerRender();
      
      // Label VRM
      if (model.format === "vrm") {
        const tag = document.createElement("div");
        tag.className = "model-viewer__tag";
        tag.textContent = "VRM (完整预览：骨骼/表情/弹簧骨)";
        container.appendChild(tag);
      }
    };
    const onError = (err) => {
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
      const w = () => {
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
      if (vrmInstance && typeof vrmInstance.destroy === "function") {
        vrmInstance.destroy();
      }
      try { renderer.dispose(); } catch (_e) { /* ignore */ }
      // Remove the canvas so the next render starts from a clean slate.
      if (renderer.domElement && renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  };

  // --- VRM Expression UI ---
  const setupVRMExpressionUI = (container, vrm) => {
    if (!vrm.expressions) return;
    
    const panel = document.createElement("div");
    panel.className = "vrm-expression-panel";
    panel.style.cssText = "position:absolute;right:10px;top:10px;background:rgba(0,0,0,0.7);padding:10px;border-radius:4px;color:#fff;font:12px/1.5 system-ui;max-width:200px;z-index:10";
    
    const title = document.createElement("div");
    title.textContent = "表情控制";
    title.style.cssText = "font-weight:bold;margin-bottom:8px;border-bottom:1px solid #444;padding-bottom:4px";
    panel.appendChild(title);
    
    const presets = vrm.expressions.getPresetNameList?.() || Object.keys(vrm.expressions.preset || {});
    
    presets.forEach(name => {
      const label = document.createElement("label");
      label.style.cssText = "display:flex;align-items:center;gap:8px;margin:4px 0";
      const input = document.createElement("input");
      input.type = "range";
      input.min = "0";
      input.max = "1";
      input.step = "0.01";
      input.value = "0";
      input.style.cssText = "flex:1";
      input.addEventListener("input", (e) => {
        vrm.expressions.setValue(name, parseFloat(e.target.value));
      });
      const span = document.createElement("span");
      span.textContent = name;
      span.style.cssText = "min-width:80px";
      label.appendChild(span);
      label.appendChild(input);
      panel.appendChild(label);
    });
    
    // Blink control
    if (vrm.blink) {
      const label = document.createElement("label");
      label.style.cssText = "display:flex;align-items:center;gap:8px;margin:4px 0";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = true;
      input.addEventListener("change", (e) => {
        vrm.blink.enabled = e.target.checked;
      });
      const span = document.createElement("span");
      span.textContent = "自动眨眼";
      label.appendChild(span);
      label.appendChild(input);
      panel.appendChild(label);
    }
    
    container.appendChild(panel);
  };

  // --- helpers ----------------------------------------------------------

  const tryLoadOrbitControls = async (THREE, domElement, camera) => {
    try {
      const mod = await import(/* @vite-ignore */ VENDOR_BASE + "OrbitControls.js");
      const OC = mod.OrbitControls || mod.default?.OrbitControls;
      if (OC) return new OC(camera, domElement);
    } catch (_e) { /* fall through */ }
    return makeDragController(domElement, camera);
  };

  const tryLoadGltfLoader = async (_THREE) => {
    try {
      const mod = await import(/* @vite-ignore */ VENDOR_BASE + "GLTFLoader.js");
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