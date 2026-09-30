// Date Town: a tiny voxel world where the agents go on their dates.
// Three.js, no build step, no assets. Every block, tree and person is a box,
// and each little person's head wears their real profile photo.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

const W = 21, H = 14;                                   // half-size of the island, in blocks
const toWorld = (x, y) => new THREE.Vector3((x - 50) * 0.36, 0, (y - 50) * 0.24);
const hash = s => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const seeded = seed => () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// what the two of them do at each kind of place
const MODES = {cafe: 'sit', dinner: 'sit', comedy: 'sit', cinema: 'watch', club: 'dance', concert: 'dance', runclub: 'jog',
  park: 'stroll', promenade: 'stroll', market: 'stroll', cricket: 'cheer', climb: 'cheer'};
const WALLS = {1: ['#ffe8cc', '#e76f51'], 2: ['#ecdcff', '#8b5cf6'], 3: ['#26324a', '#f2b33d']};
const MOODS = {1: {bg: '#cfeaff', hemi: 1.9, sun: 2.4, sunColor: '#fff4e0', glow: 0},
               2: {bg: '#ffd9b0', hemi: 1.6, sun: 1.9, sunColor: '#ffbf80', glow: .35},
               3: {bg: '#2b2754', hemi: .75, sun: .55, sunColor: '#a3b6ff', glow: 1.6}};
const SHIRTS = ['#f87171', '#fb923c', '#facc15', '#4ade80', '#38bdf8', '#818cf8', '#e879f9', '#f472b6', '#2dd4bf'];
const PANTS = ['#1e3a8a', '#374151', '#7c2d12', '#1f2937', '#4c1d95'];

const CSS = `
.town{position:relative;overflow:hidden;border-radius:1.25rem;touch-action:none}
.town canvas{display:block}
.town-labels{position:absolute;inset:0;pointer-events:none}
.l3{width:0;height:0;position:relative}
.l3>*{position:absolute;bottom:0;left:50%;transform:translateX(-50%);white-space:nowrap}
.l3 .tag{font:700 11px Inter,sans-serif;color:#fff;background:rgba(43,26,32,.72);padding:1px 7px;border-radius:6px}
.l3 .say{white-space:normal;width:max-content;max-width:190px;font:500 11.5px/1.33 Inter,sans-serif;color:#2b1a20;background:#fff;padding:6px 9px;border-radius:12px;box-shadow:0 3px 12px rgba(0,0,0,.2);display:none}
.l3 .say.on{display:block;animation:pop3 .25s ease both}
.l3 .say:after{content:'';position:absolute;left:50%;bottom:-6px;margin-left:-6px;border:6px solid transparent;border-top-color:#fff;border-bottom:0}
.l3 .emo{font-size:24px;animation:float3 2s ease-out forwards}
.l3 .emo b{font:700 12px Inter,sans-serif;background:#fff;color:#2b1a20;border-radius:6px;padding:1px 6px;margin-left:3px;vertical-align:middle}
.l3 .day{font:800 10px Inter,sans-serif;color:#fff;background:#e11d48;padding:1px 7px;border-radius:99px;box-shadow:0 1px 4px rgba(0,0,0,.25)}
@keyframes pop3{from{transform:translateX(-50%) scale(.6);opacity:0}to{transform:translateX(-50%) scale(1);opacity:1}}
@keyframes float3{0%{transform:translate(-50%,0) scale(.5);opacity:0}15%{opacity:1;transform:translate(-50%,-8px) scale(1.15)}100%{transform:translate(-50%,-70px) scale(1);opacity:0}}`;

export async function createTown(el, {venues, base = ''}) {
  if (!document.getElementById('town-css')) document.head.insertAdjacentHTML('beforeend', `<style id="town-css">${CSS}</style>`);
  el.classList.add('town');
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(MOODS[1].bg);
  const renderer = new THREE.WebGLRenderer({antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  el.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = 'town-labels';
  el.appendChild(labels.domElement);

  const VIEW = 30;
  const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 300);
  cam.position.set(26, 28, 26);
  const controls = new OrbitControls(cam, renderer.domElement);
  Object.assign(controls, {enableDamping: true, enablePan: false, enableZoom: false, minPolarAngle: Math.PI / 6, maxPolarAngle: Math.PI / 2.7});
  const resize = () => {
    const w = el.clientWidth, h = el.clientHeight || 1, a = w / h;
    Object.assign(cam, {left: -VIEW * a / 2, right: VIEW * a / 2, top: VIEW / 2, bottom: -VIEW / 2});
    cam.updateProjectionMatrix(); renderer.setSize(w, h); labels.setSize(w, h);
  };
  new ResizeObserver(resize).observe(el); resize();

  const hemi = new THREE.HemisphereLight('#ffffff', '#5d7f4f', MOODS[1].hemi);
  const sun = new THREE.DirectionalLight(MOODS[1].sunColor, MOODS[1].sun);
  sun.position.set(-16, 30, 14); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0006;
  Object.assign(sun.shadow.camera, {left: -28, right: 28, top: 22, bottom: -22, near: 1, far: 90});
  scene.add(hemi, sun);

  const lambert = c => new THREE.MeshLambertMaterial({color: c});
  const box = (parent, w, h, d, mat, x, y, z) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), typeof mat === 'string' ? lambert(mat) : mat);
    m.position.set(x, y, z); m.castShadow = m.receiveShadow = true; parent.add(m); return m;
  };
  const label = (html, y, parent) => {
    const div = document.createElement('div'); div.className = 'l3'; div.innerHTML = html;
    const o = new CSS2DObject(div); o.position.set(0, y, 0); parent.add(o); return o;
  };

  // ---------- the island: grass blocks, patios, a pond, trees and flowers
  const V = venues.map(v => ({...v, pos: toWorld(v.x, v.y)}));
  const kind = new Map(), key = (x, z) => `${Math.round(x)},${Math.round(z)}`;
  for (const v of V) {
    for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 1; dz++) kind.set(key(v.pos.x + dx, v.pos.z + dz), 'building');
    for (let dx = -2; dx <= 2; dx++) for (let dz = 2; dz <= 3; dz++) kind.set(key(v.pos.x + dx, v.pos.z + dz), 'path');
  }
  for (let x = -W; x <= W; x++) for (let z = -H; z <= H; z++)
    if (((x + 18.5) / 3.6) ** 2 + ((z + 11.6) / 2.2) ** 2 < 1 && !kind.has(`${x},${z}`)) kind.set(`${x},${z}`, 'water');
  const tiles = [];
  for (let x = -W; x <= W; x++) for (let z = -H; z <= H; z++) tiles.push([x, z]);
  const ground = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), lambert('#ffffff'), tiles.length);
  const m4 = new THREE.Matrix4(), col = new THREE.Color(), r = seeded(42);
  tiles.forEach(([x, z], i) => {
    const k = kind.get(`${x},${z}`);
    m4.makeTranslation(x, k === 'water' ? -0.62 : -0.5, z); ground.setMatrixAt(i, m4);
    col.set(k === 'path' ? ['#ecd9b0', '#e3cc9a'][i % 2] : k === 'water' ? ['#6fc9f3', '#5cbcec'][Math.floor(r() * 2)]
      : ['#86cc5a', '#7cc350', '#8fd262', '#79bf4c'][Math.floor(r() * 4)]);
    ground.setColorAt(i, col);
  });
  ground.receiveShadow = true; scene.add(ground);
  box(scene, 2 * W + 1, 1.3, 2 * H + 1, '#8a5a3b', 0, -1.65, 0).castShadow = false;   // dirt
  box(scene, 2 * W + 1, 0.9, 2 * H + 1, '#7b7b7b', 0, -2.75, 0).castShadow = false;   // stone

  const free = [];
  for (let x = -W + 1; x < W; x++) for (let z = -H + 1; z < H; z++) {
    let ok = !kind.has(`${x},${z}`);
    for (let dx = -1; dx <= 1 && ok; dx++) for (let dz = -1; dz <= 1 && ok; dz++) if (kind.has(`${x + dx},${z + dz}`)) ok = false;
    if (ok) free.push([x, z]);
  }
  const pick = n => { const out = []; for (let i = 0; i < n && free.length; i++) out.push(free.splice(Math.floor(r() * free.length), 1)[0]); return out; };
  const trees = pick(46);
  const trunk = new THREE.InstancedMesh(new THREE.BoxGeometry(.32, .9, .32), lambert('#8b5a2b'), trees.length);
  const leaf = new THREE.InstancedMesh(new THREE.BoxGeometry(1.15, 1, 1.15), lambert('#ffffff'), trees.length);
  const leafTop = new THREE.InstancedMesh(new THREE.BoxGeometry(.7, .5, .7), lambert('#ffffff'), trees.length);
  trees.forEach(([x, z], i) => {
    const s = .8 + r() * .5;
    m4.compose(new THREE.Vector3(x, .45, z), new THREE.Quaternion(), new THREE.Vector3(1, s, 1)); trunk.setMatrixAt(i, m4);
    m4.compose(new THREE.Vector3(x, .9 * s + .5, z), new THREE.Quaternion(), new THREE.Vector3(s, s, s)); leaf.setMatrixAt(i, m4);
    m4.compose(new THREE.Vector3(x, .9 * s + 1.2 * s, z), new THREE.Quaternion(), new THREE.Vector3(s, s, s)); leafTop.setMatrixAt(i, m4);
    col.set(['#3fa34d', '#4bb35a', '#2f8f45', '#f9a8d4'][r() < .12 ? 3 : Math.floor(r() * 3)]); leaf.setColorAt(i, col); leafTop.setColorAt(i, col);
  });
  for (const m of [trunk, leaf, leafTop]) { m.castShadow = m.receiveShadow = true; scene.add(m); }
  const flowers = pick(80);
  const flower = new THREE.InstancedMesh(new THREE.BoxGeometry(.2, .2, .2), lambert('#ffffff'), flowers.length);
  flowers.forEach(([x, z], i) => {
    m4.makeTranslation(x + (r() - .5) * .6, .1, z + (r() - .5) * .6); flower.setMatrixAt(i, m4);
    col.set(['#f43f5e', '#facc15', '#ffffff', '#a78bfa', '#fb923c'][Math.floor(r() * 5)]); flower.setColorAt(i, col);
  });
  scene.add(flower);

  // ---------- the venues: little shops with glowing windows, signs and a few props
  const windowMat = new THREE.MeshLambertMaterial({color: '#fff6c8', emissive: '#ffcf5a', emissiveIntensity: 0});
  const floorMats = [];
  const signFor = v => {
    const c = document.createElement('canvas'); c.width = 512; c.height = 150; const x = c.getContext('2d');
    x.fillStyle = 'rgba(255,255,255,.96)'; x.beginPath(); x.roundRect(6, 6, 500, 138, 40); x.fill();
    x.textBaseline = 'middle'; x.font = '82px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif'; x.fillText(v.emoji, 26, 80);
    x.fillStyle = '#3b2a30'; x.font = 'bold 40px Inter, Helvetica, sans-serif';
    x.fillText(v.name.length > 18 ? v.name.slice(0, 17) + '…' : v.name, 128, 78);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({map: t, transparent: true})); s.scale.set(3.7, 1.08, 1); return s;
  };
  for (const v of V) {
    const [wall, roof] = WALLS[v.tier] || WALLS[1], mode = MODES[v.id] || 'idle';
    const g = new THREE.Group(); g.position.copy(v.pos); scene.add(g); v.group = g;
    box(g, 2.8, 1.9, 2.3, wall, 0, .95, -.3);
    box(g, 3.2, .45, 2.7, roof, 0, 2.12, -.3);
    box(g, 2.3, .35, 1.9, roof, 0, 2.5, -.3);
    box(g, .66, 1.05, .08, '#6b4430', 0, .52, .86);
    for (const sx of [-.88, .88]) box(g, .55, .5, .08, windowMat, sx, 1.2, .86);
    const sign = signFor(v); sign.position.set(0, 3.45, -.3); g.add(sign);
    if (mode === 'sit') {
      box(g, .8, .62, .8, '#b07a4f', 0, .31, 2.4);
      if (v.id === 'dinner') box(g, .12, .22, .12, new THREE.MeshLambertMaterial({color: '#fff1b8', emissive: '#ffb020', emissiveIntensity: 1}), 0, .74, 2.4);
      else for (const cx of [-.2, .2]) box(g, .14, .17, .14, '#ffffff', cx, .7, 2.4);
      for (const sx of [-1.0, 1.0]) box(g, .46, .42, .46, '#8b5e3c', sx, .21, 2.4);
    }
    if (mode === 'watch') {
      box(g, 1.9, 1.1, .06, new THREE.MeshLambertMaterial({color: '#1f2937', emissive: '#60a5fa', emissiveIntensity: .5}), 0, 1.25, .9);
      for (const sx of [-.58, .58]) box(g, .46, .42, .46, '#be123c', sx, .21, 3.0);
    }
    if (mode === 'dance') for (let i = 0; i < 6; i++) {
      const fm = new THREE.MeshLambertMaterial({color: '#ffffff', emissive: ['#f472b6', '#60a5fa', '#facc15'][i % 3], emissiveIntensity: .3});
      floorMats.push(fm); box(g, .95, .06, .95, fm, -1 + (i % 3), .03, 2.1 + Math.floor(i / 3));
    }
    const badge = label('<div class="day" style="display:none"></div>', 4.1, g); v.badge = badge.element.firstElementChild;
  }
  const spotsFor = (v, mode) => {
    const at = (x, z, face) => ({pos: v.pos.clone().add(new THREE.Vector3(x, 0, z)), face});
    if (mode === 'sit') return [at(-1.0, 2.4, Math.PI / 2), at(1.0, 2.4, -Math.PI / 2)];
    if (mode === 'watch') return [at(-.58, 3.0, Math.PI), at(.58, 3.0, Math.PI)];
    return [at(-.85, 2.7, Math.PI / 2), at(.85, 2.7, -Math.PI / 2)];
  };

  // ---------- the people: blocky chibi figures with a photo face
  const people = {};
  const faceTex = (url, letter, bg) => new Promise(done => {
    const c = document.createElement('canvas'); c.width = c.height = 128; const x = c.getContext('2d');
    const finish = side => { const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; done({t, side}); };
    const img = new Image();
    img.onload = () => {
      const s = Math.min(img.width, img.height);
      x.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, 128, 128);
      const d = x.getImageData(0, 0, 128, 128).data; let R = 0, G = 0, B = 0, n = 0;
      for (let i = 0; i < 128; i += 4) for (const [px, py] of [[i, 0], [i, 127], [0, i], [127, i]]) { const o = (py * 128 + px) * 4; R += d[o]; G += d[o + 1]; B += d[o + 2]; n++; }
      finish(`rgb(${R / n | 0},${G / n | 0},${B / n | 0})`);
    };
    img.onerror = () => {
      x.fillStyle = bg; x.fillRect(0, 0, 128, 128); x.fillStyle = '#fff'; x.font = 'bold 72px Inter, sans-serif';
      x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(letter, 64, 70); finish(bg);
    };
    img.src = url;
  });
  const limb = (parent, w, h, d, mat, x, y) => {
    const pivot = new THREE.Group(); pivot.position.set(x, y, 0); parent.add(pivot);
    const m = box(pivot, w, h, d, mat, 0, -h / 2, 0); return pivot;
  };
  function addPerson(k, id, name) {
    const h = hash(id), g = new THREE.Group(); g.userData.key = k;
    const shirt = lambert(SHIRTS[h % SHIRTS.length]), pants = lambert(PANTS[(h >>> 4) % PANTS.length]);
    const legL = limb(g, .2, .42, .22, pants, -.12, .42), legR = limb(g, .2, .42, .22, pants, .12, .42);
    box(g, .5, .5, .3, shirt, 0, .67, 0);
    const armL = limb(g, .15, .44, .17, shirt, -.33, .9), armR = limb(g, .15, .44, .17, shirt, .33, .9);
    const side = lambert('#6b4a3a'), top = lambert('#5a3d30'), face = lambert('#ffffff');
    box(g, .66, .66, .66, [side, side, top, side, face, side], 0, 1.25, 0);
    faceTex(`${base}/photos/${id}.jpg`, (name || id)[0].toUpperCase(), SHIRTS[h % SHIRTS.length]).then(({t, side: sc}) => {
      face.map = t; face.needsUpdate = true; side.color.set(sc); top.color.set(sc).multiplyScalar(.8);
    });
    label(`<div class="tag">${esc(name)}</div>`, 1.72, g);
    const bubble = label('<div class="say"></div>', 1.98, g).element.firstElementChild;
    g.scale.setScalar(1.45);
    g.position.set((Object.keys(people).length % 2 ? .8 : -.8), 0, 12.5);
    scene.add(g);
    const p = {k, id, g, legL, legR, armL, armR, bubble, target: g.position.clone(), speed: 3, face: Math.PI, heading: 0,
               mode: 'idle', phase: (h % 628) / 100, walk: 0, hop: 0, spot: null};
    people[k] = p; return p;
  }

  // ---------- behaviour
  let current = null, follow = new THREE.Vector3(0, 0, 0), zoomTo = 1, moodTo = MOODS[1], wanderOn = false;
  const turn = (a, b) => { let d = a - b; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return d; };
  function updatePerson(p, dt, t) {
    const pos = p.g.position;
    if (p.mode === 'stroll' && p.spot && !p.travel) p.target.set(p.spot.x + Math.cos(t * .6 + p.phase) * 1.1, 0, p.spot.z + .4 + Math.sin(t * .6 + p.phase) * .5);
    const to = new THREE.Vector3(p.target.x - pos.x, 0, p.target.z - pos.z), dist = to.length();
    const moving = dist > .05;
    if (moving) { pos.addScaledVector(to.normalize(), Math.min(dist, p.speed * dt)); p.heading = Math.atan2(to.x, to.z); }
    else p.travel = false;
    if (wanderOn && !moving) {
      if (!p.wait) p.wait = t + 1 + Math.random() * 3;
      else if (t > p.wait) { p.wait = 0; const v = V[Math.floor(Math.random() * V.length)]; p.target.set(v.pos.x + (Math.random() - .5) * 3, 0, v.pos.z + 2.3 + Math.random() * 1.3); p.speed = 1.6 + Math.random(); }
    }
    p.g.rotation.y += turn(moving ? p.heading : p.face, p.g.rotation.y) * Math.min(1, dt * 9);
    let swing = 0, lift = 0, arms = null, legs = null;
    if (moving) { p.walk += dt * 11; swing = Math.sin(p.walk) * .7; lift = Math.abs(Math.sin(p.walk)) * .04; }
    else if (p.mode === 'sit' || p.mode === 'watch') { legs = -1.45; lift = -.17; arms = p.mode === 'sit' ? -.5 : 0; }
    else if (p.mode === 'dance') { lift = Math.abs(Math.sin(t * 6 + p.phase)) * .2; arms = -2.5 + Math.sin(t * 6) * .5; p.g.rotation.y += dt * 1.5; }
    else if (p.mode === 'jog') { swing = Math.sin(t * 14 + p.phase) * .65; lift = Math.abs(Math.sin(t * 14)) * .07; }
    else if (p.mode === 'cheer') { arms = -2.8; lift = Math.max(0, Math.sin(t * 5 + p.phase)) * .28; }
    else lift = Math.sin(t * 2 + p.phase) * .015;
    if (p.hop > 0) { lift += Math.sin((1 - p.hop) * Math.PI) * .32; p.hop = Math.max(0, p.hop - dt * 2.4); }
    p.legL.rotation.x = legs ?? swing; p.legR.rotation.x = legs ?? -swing;
    p.armL.rotation.x = arms ?? -swing * .8; p.armR.rotation.x = arms ?? swing * .8;
    pos.y = lift;
  }
  const clock = new THREE.Clock();
  const moodNow = {bg: new THREE.Color(MOODS[1].bg), sun: new THREE.Color(MOODS[1].sunColor), hemi: MOODS[1].hemi, sunI: MOODS[1].sun, glow: 0};
  (function tick() {
    const dt = Math.min(clock.getDelta(), .05), t = clock.elapsedTime, k = Math.min(1, dt * 2.2);
    for (const p of Object.values(people)) updatePerson(p, dt, t);
    moodNow.bg.lerp(new THREE.Color(moodTo.bg), k); scene.background.copy(moodNow.bg);
    moodNow.sun.lerp(new THREE.Color(moodTo.sunColor), k); sun.color.copy(moodNow.sun);
    moodNow.hemi += (moodTo.hemi - moodNow.hemi) * k; hemi.intensity = moodNow.hemi;
    moodNow.sunI += (moodTo.sun - moodNow.sunI) * k; sun.intensity = moodNow.sunI;
    moodNow.glow += (moodTo.glow - moodNow.glow) * k; windowMat.emissiveIntensity = moodNow.glow;
    floorMats.forEach((m, i) => { m.emissiveIntensity = .25 + moodNow.glow * (.5 + .5 * Math.sin(t * 4 + i * 1.7)); });
    const d = follow.clone().sub(controls.target).multiplyScalar(Math.min(1, dt * 2.5));
    controls.target.add(d); cam.position.add(d);
    cam.zoom += (zoomTo - cam.zoom) * Math.min(1, dt * 2); cam.updateProjectionMatrix();
    controls.update(); renderer.render(scene, cam); labels.render(scene, cam);
    requestAnimationFrame(tick);
  })();

  let trail = null;
  const api = {
    people, addPerson,
    goTo(id, seconds = 0) {
      const v = V.find(x => x.id === id); if (!v || current === v) return;
      current = v; const mode = MODES[id] || 'idle', spots = spotsFor(v, mode);
      Object.values(people).forEach((p, i) => {
        const s = spots[i % 2]; p.spot = s.pos; p.face = s.face; p.mode = mode;
        if (seconds <= 0) { p.g.position.copy(s.pos); p.target.copy(s.pos); p.g.rotation.y = s.face; p.travel = false; }
        else { p.target.copy(s.pos); p.travel = true; p.speed = Math.max(2.2, p.g.position.distanceTo(s.pos) / seconds); }
      });
      api.focus(v.pos.clone().add(new THREE.Vector3(0, .6, 2.4)), 2.7);
    },
    part() {  // no second date: they wave and walk off in different directions
      current = null;
      Object.values(people).forEach((p, i) => { p.mode = 'idle'; p.travel = true; p.speed = 2;
        p.target.copy(p.g.position).add(new THREE.Vector3(i ? 5 : -5, 0, 2.5)); p.face = i ? -Math.PI / 2 : Math.PI / 2; });
    },
    say(k, text) {
      Object.values(people).forEach(p => p.bubble.classList.remove('on'));
      const p = people[k]; if (!p || !text) return;
      p.bubble.textContent = text.length > 105 ? text.slice(0, 102).replace(/\s+\S*$/, '') + '…' : text; p.bubble.classList.add('on'); p.hop = 1;
    },
    clearSay() { Object.values(people).forEach(p => p.bubble.classList.remove('on')); },
    emote(k, emoji, text = '') {
      const p = people[k]; if (!p) return;
      const o = label(`<div class="emo" style="margin-left:${(Math.random() * 30 - 15) | 0}px">${emoji}${text ? `<b>${esc(text)}</b>` : ''}</div>`, 2.1, p.g);
      setTimeout(() => { p.g.remove(o); o.element.remove(); }, 2000);
    },
    burst(emojis, n = 14) {
      Object.values(people).forEach(p => { p.hop = 1; p.mode = 'cheer'; });
      for (let i = 0; i < n; i++) setTimeout(() => api.emote(i % 2 ? 'a' : 'b', emojis[i % emojis.length]), i * 140);
    },
    setVisited(list) {
      V.forEach(v => { v.badge.style.display = 'none'; });
      list.forEach(({id, day}) => { const v = V.find(x => x.id === id); if (v) { v.badge.textContent = `Day ${day}`; v.badge.style.display = ''; } });
      if (trail) { scene.remove(trail); trail.geometry.dispose(); }
      const pts = list.map(({id}) => V.find(x => x.id === id)).filter(Boolean).map(v => v.pos.clone().add(new THREE.Vector3(0, 0, 2.6)));
      const steps = [];
      for (let i = 1; i < pts.length; i++) { const n = Math.floor(pts[i - 1].distanceTo(pts[i]) / .75); for (let j = 1; j < n; j++) steps.push(pts[i - 1].clone().lerp(pts[i], j / n)); }
      if (!steps.length) return;
      trail = new THREE.InstancedMesh(new THREE.BoxGeometry(.3, .04, .3), new THREE.MeshLambertMaterial({color: '#fb7185', emissive: '#e11d48', emissiveIntensity: .35}), steps.length);
      steps.forEach((s, i) => { m4.makeTranslation(s.x, .03, s.z); trail.setMatrixAt(i, m4); });
      scene.add(trail);
    },
    setMood(tier) { moodTo = MOODS[tier] || MOODS[1]; },
    focus(v, zoom = 1.6) { follow.copy(v); zoomTo = zoom; },
    overview() { follow.set(0, 0, 0); zoomTo = 1; },
    wander() {
      wanderOn = true;
      Object.values(people).forEach(p => { const v = V[Math.floor(Math.random() * V.length)];
        p.g.position.set(v.pos.x + (Math.random() - .5) * 3, 0, v.pos.z + 2.5 + Math.random()); p.target.copy(p.g.position); p.mode = 'idle'; });
    },
    onPick(fn) {
      let down = null; const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
      renderer.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
      renderer.domElement.addEventListener('click', e => {
        if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
        const rc = renderer.domElement.getBoundingClientRect();
        ndc.set((e.clientX - rc.left) / rc.width * 2 - 1, -(e.clientY - rc.top) / rc.height * 2 + 1); ray.setFromCamera(ndc, cam);
        const hit = ray.intersectObjects(Object.values(people).map(p => p.g), true)[0];
        let o = hit?.object; while (o && !o.userData.key) o = o.parent; if (o) fn(o.userData.key);
      });
    },
  };
  return api;
}
