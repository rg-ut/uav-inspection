<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { observationTargets, candidateScore, viewSector, type ViewStrategy, type Position3, type ViewpointResult } from '../../utils/pestSimulation'

const props = defineProps<{ points: Position3[]; position: Position3; heading: number; running: boolean; cameraMode: string; targetIndex: number; strategy: ViewStrategy; visitedSectors: number[] }>()
const emit = defineEmits<{ advance: [seconds: number]; ready: []; error: [message: string]; suspend: [] }>()
const host = ref<HTMLDivElement | null>(null)
const error = ref('')
let renderer: THREE.WebGLRenderer | undefined
let scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: OrbitControls
let drone: THREE.Group, routeLine: THREE.Line, marker: THREE.Mesh
const rotors: THREE.Mesh[] = [], obstacles: THREE.Object3D[] = []
let resize: ResizeObserver | undefined, frame = 0, previousTime = 0, disposed = false
let transition: { start: number; from: THREE.Vector3; to: THREE.Vector3; targetFrom: THREE.Vector3; target: THREE.Vector3; result: ViewpointResult; finish: (result: ViewpointResult | null) => void } | null = null
function leafFocus(index: number) {
  const [x, , z] = observationTargets[index]!.point
  const variation = 1 + Math.sin(x * 7 + z * 3) * .07, angle = 7.2 + x * .1
  return new THREE.Vector3(x + Math.cos(angle) * 1.3 * variation * .94, 3.06 * variation, z - Math.sin(angle) * 1.3 * variation * .94)
}
const targetPoint = () => leafFocus(props.targetIndex)

function mesh(geometry: THREE.BufferGeometry, color: number, x: number, y: number, z: number, parent: THREE.Object3D = scene) {
  const object = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness: .85 }))
  object.position.set(x, y, z); object.castShadow = true; object.receiveShadow = true; parent.add(object)
  return object
}
function updateRoute() {
  if (!scene) return
  if (routeLine) { scene.remove(routeLine); routeLine.geometry.dispose(); (routeLine.material as THREE.Material).dispose() }
  routeLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(props.points.map(p => new THREE.Vector3(...p))), new THREE.LineBasicMaterial({ color: 0x36d5bc, transparent: true, opacity: .9 }))
  scene.add(routeLine)
}
function updateTarget() { if (marker) { const [x, , z] = observationTargets[props.targetIndex]!.point; marker.position.set(x, .3, z) } }
function resetView() {
  if (!camera) return
  transition?.finish(null); transition = null
  camera.position.set(46, 37, 45); controls.target.set(0, 1, -4); controls.update()
}
function occlusion(position: THREE.Vector3, target: THREE.Vector3) {
  const ray = new THREE.Raycaster()
  let blocked = 0
  for (const x of [-.8, 0, .8]) for (const y of [-.5, 0, .5]) {
    const point = target.clone().add(new THREE.Vector3(x, y, 0))
    const direction = point.clone().sub(position)
    ray.set(position, direction.normalize()); ray.far = position.distanceTo(point) - .4
    if (ray.intersectObjects(obstacles, false).length) blocked++
  }
  return blocked / 9
}
function optimizeView(): Promise<ViewpointResult | null> {
  if (!renderer || disposed) return Promise.resolve(null)
  transition?.finish(null)
  scene.updateMatrixWorld(true)
  const target = targetPoint(), before = occlusion(camera.position, target)
  const candidates = Array.from({ length: 36 }, (_, index) => {
    const angle = index % 12 * Math.PI / 6
    const position = target.clone().add(new THREE.Vector3(Math.cos(angle) * 6, [1, 2.6, 4.2][Math.floor(index / 12)]!, Math.sin(angle) * 6))
    const blocked = occlusion(position, target), sector = viewSector(position.toArray() as Position3, observationTargets[props.targetIndex]!.point)
    return { position, blocked, sector, score: candidateScore(props.strategy, 1 - blocked, !props.visitedSectors.includes(sector), observationTargets[props.targetIndex]!.cue) }
  })
  if (props.strategy !== 'fixed') candidates.sort((a, b) => b.score - a.score || a.blocked - b.blocked)
  const best = candidates[0]!, destination = best.position
  const direction = target.clone().sub(destination)
  const result: ViewpointResult = { before, after: best.blocked, candidates: props.strategy === 'fixed' ? 1 : candidates.length, position: destination.toArray() as Position3, strategy: props.strategy, target: props.targetIndex, sector: best.sector, yaw: Math.atan2(direction.x, direction.z) * 180 / Math.PI, pitch: Math.atan2(direction.y, Math.hypot(direction.x, direction.z)) * 180 / Math.PI }
  return new Promise(resolve => {
    transition = { start: performance.now(), from: camera.position.clone(), to: destination, targetFrom: controls.target.clone(), target, result, finish: resolve }
  })
}
function changeCamera() {
  if (!camera) return
  if (props.cameraMode === 'overview') resetView()
  if (props.cameraMode === 'top') { camera.position.set(0, 92, .1); controls.target.set(0, 0, 0); controls.update() }
}
function visibilityChange() { previousTime = 0; if (document.hidden) emit('suspend') }
function contextLost(event: Event) { event.preventDefault(); error.value = '三维渲染已中断，请刷新页面恢复。巡航已暂停。'; emit('suspend'); emit('error', error.value) }

onMounted(() => {
  if (!host.value) return
  try {
    scene = new THREE.Scene(); scene.background = new THREE.Color(0xdbe9e5); scene.fog = new THREE.Fog(0xdbe9e5, 110, 200)
    camera = new THREE.PerspectiveCamera(48, 1, .1, 260)
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.7)); renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.setAttribute('aria-label', '玉米植株与无人机三维仿真场景')
    renderer.domElement.addEventListener('webglcontextlost', contextLost)
    host.value.appendChild(renderer.domElement)
    controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.maxPolarAngle = Math.PI / 2.05; controls.minDistance = 5; controls.maxDistance = 145
    scene.add(new THREE.HemisphereLight(0xf3fffc, 0x6d8258, 2.5))
    const sun = new THREE.DirectionalLight(0xfff0d3, 3); sun.position.set(30, 65, 35); sun.castShadow = true
    sun.shadow.camera.left = -55; sun.shadow.camera.right = 55; sun.shadow.camera.top = 55; sun.shadow.camera.bottom = -55; sun.shadow.mapSize.set(2048, 2048); sun.shadow.normalBias = .06; sun.shadow.bias = -.0002; scene.add(sun)
    mesh(new THREE.BoxGeometry(100, .4, 95), 0x90aa7b, 0, -.3, 0)
    mesh(new THREE.BoxGeometry(6, .12, 85), 0xd2c6a9, 0, .02, 0)
    mesh(new THREE.BoxGeometry(78, .12, 5), 0xd2c6a9, 0, .04, 26)
    // Procedural maize plants. Coloured leaf patches are authored symptoms, never detections.
    const leafGeometry = new THREE.BufferGeometry()
    const vertices: number[] = [], indices: number[] = [], uvs: number[] = []
    for (let section = 0; section <= 8; section++) {
      const t = section / 8, width = .4 * Math.sin(Math.PI * t)
      for (const side of [-1, 1]) { vertices.push(2.6 * t, 1.5 * t - 2.1 * t * t + width * .15, side * width); uvs.push(t, (side + 1) / 2) }
      if (section < 8) { const i = section * 2; indices.push(i, i + 1, i + 2, i + 1, i + 3, i + 2) }
    }
    leafGeometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2)); leafGeometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3)); leafGeometry.setIndex(indices); leafGeometry.computeVertexNormals()
    const leafMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, side: THREE.DoubleSide, roughness: .8 })
    const plantCount = 13 * 11
    const leaves = new THREE.InstancedMesh(leafGeometry, leafMaterial, plantCount * 5)
    const stems = new THREE.InstancedMesh(new THREE.CylinderGeometry(.07, .13, 4.2, 5), new THREE.MeshStandardMaterial({ color: 0x91a456 }), plantCount)
    const tasselParts: THREE.BufferGeometry[] = [new THREE.CylinderGeometry(.025, .04, .85, 4)]
    for (let arm = 0; arm < 6; arm++) { const angle = arm * Math.PI / 3; tasselParts.push(new THREE.CylinderGeometry(.014, .025, .55, 4).rotateZ(-.8).rotateY(angle).translate(Math.cos(angle) * .18, .12 + arm % 2 * .13, -Math.sin(angle) * .18)) }
    const tasselGeometry = mergeGeometries(tasselParts); tasselParts.forEach(part => part.dispose())
    const tassels = new THREE.InstancedMesh(tasselGeometry, new THREE.MeshStandardMaterial({ color: 0xc9b772 }), plantCount)
    const cobs = new THREE.InstancedMesh(new THREE.CapsuleGeometry(.14, .45, 3, 6), new THREE.MeshStandardMaterial({ color: 0xc5aa4d }), plantCount)
    const transform = new THREE.Object3D(); let plantIndex = 0
    for (let x = -24; x <= 24; x += 4) {
      mesh(new THREE.BoxGeometry(2.8, .15, 47), 0x82734d, x, .03, -4)
      for (let z = -24; z <= 16; z += 4) {
        const variation = 1 + Math.sin(x * 7 + z * 3) * .07
        transform.rotation.set(0, 0, 0); transform.scale.set(1, variation, 1); transform.position.set(x, 2.1 * variation, z); transform.updateMatrix(); stems.setMatrixAt(plantIndex, transform.matrix)
        transform.position.set(x, 4.45 * variation, z); transform.updateMatrix(); tassels.setMatrixAt(plantIndex, transform.matrix)
        transform.position.set(x + .23, 2.25 * variation, z); transform.rotation.z = -.35; transform.updateMatrix(); cobs.setMatrixAt(plantIndex, transform.matrix)
        for (let level = 0; level < 5; level++) {
          transform.rotation.set(0, level * 2.4 + x * .1, 0); transform.scale.setScalar(variation * (.85 + level * .03)); transform.position.set(x, (1.2 + level * .55) * variation, z); transform.updateMatrix()
          if (level === 3 && observationTargets.slice(0, 3).some(target => target.point[0] === x && target.point[2] === z)) { const hidden = transform.clone(); hidden.scale.setScalar(0); hidden.updateMatrix(); leaves.setMatrixAt(plantIndex * 5 + level, hidden.matrix) }
          else leaves.setMatrixAt(plantIndex * 5 + level, transform.matrix)
          leaves.setColorAt(plantIndex * 5 + level, new THREE.Color().setHSL(.25 + (plantIndex % 3) * .008, .35, .29 + level * .024))
        }
        plantIndex++
      }
    }
    for (const group of [leaves, stems, tassels, cobs]) { group.castShadow = true; group.receiveShadow = true; scene.add(group) }
    obstacles.push(leaves)
    observationTargets.forEach((target, index) => {
      const [x, y, z] = target.point
      mesh(new THREE.CylinderGeometry(.65, .65, .1, 20), index === 3 ? 0x5bbd9d : 0xe9bb55, x, .15, z)
      const label = document.createElement('canvas'); label.width = 256; label.height = 80
      const context = label.getContext('2d')!
      context.fillStyle = '#183d30'; context.fillRect(0, 0, 256, 80)
      context.fillStyle = '#fff4c0'; context.font = 'bold 40px sans-serif'; context.textAlign = 'center'; context.fillText(`M0${index + 1}`, 128, 55)
      const texture = new THREE.CanvasTexture(label); texture.colorSpace = THREE.SRGBColorSpace
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false }))
      sprite.position.set(x, y + 3.3, z); sprite.scale.set(3.5, 1.1, 1); scene.add(sprite)
      if (index !== 3) {
        const leafCanvas = document.createElement('canvas'); leafCanvas.width = 512; leafCanvas.height = 128
        const paint = leafCanvas.getContext('2d')!
        paint.fillStyle = '#638b43'; paint.fillRect(0, 0, 512, 128)
        paint.strokeStyle = '#94ab60'; paint.lineWidth = 2; paint.beginPath(); paint.moveTo(0, 64); paint.lineTo(512, 64); paint.stroke()
        for (let spot = 0; spot < 9; spot++) {
          paint.fillStyle = '#bfa25f'; paint.beginPath(); paint.ellipse(70 + spot * 43, 28 + spot % 3 * 29, index === 1 ? 30 : 17, 9, -.15, 0, Math.PI * 2); paint.fill()
          paint.fillStyle = '#795234'; paint.beginPath(); paint.ellipse(70 + spot * 43, 28 + spot % 3 * 29, index === 1 ? 25 : 12, 5, -.15, 0, Math.PI * 2); paint.fill()
        }
        const leafTexture = new THREE.CanvasTexture(leafCanvas); leafTexture.colorSpace = THREE.SRGBColorSpace
        const symptomLeaf = new THREE.Mesh(leafGeometry, new THREE.MeshStandardMaterial({ map: leafTexture, side: THREE.DoubleSide, roughness: .9 }))
        const variation = 1 + Math.sin(x * 7 + z * 3) * .07
        symptomLeaf.position.set(x, 2.85 * variation, z); symptomLeaf.rotation.y = 7.2 + x * .1; symptomLeaf.scale.setScalar(variation * .94); symptomLeaf.castShadow = true; scene.add(symptomLeaf); obstacles.push(symptomLeaf)
      }
    })
    const building = mesh(new THREE.BoxGeometry(8, 4.5, 7), 0xe3dbc6, -34, 2.25, -31); obstacles.push(building)
    mesh(new THREE.BoxGeometry(9, .55, 8), 0x6d8177, -34, 4.8, -31)
    for (let x = -42; x <= 42; x += 7) { mesh(new THREE.BoxGeometry(.3, 2, .3), 0xb4a78b, x, 1, -40); mesh(new THREE.BoxGeometry(.3, 2, .3), 0xb4a78b, x, 1, 39) }
    mesh(new THREE.CylinderGeometry(3.5, 3.5, .12, 32), 0xe6dfc9, 0, .1, 33)
    mesh(new THREE.BoxGeometry(.35, .15, 3), 0x4b7971, -1, .2, 33); mesh(new THREE.BoxGeometry(.35, .15, 3), 0x4b7971, 1, .2, 33); mesh(new THREE.BoxGeometry(2, .15, .35), 0x4b7971, 0, .2, 33)
    drone = new THREE.Group(); scene.add(drone)
    mesh(new THREE.BoxGeometry(1.5, .5, 2), 0xf4f7f1, 0, 0, 0, drone)
    for (const angle of [Math.PI / 4, -Math.PI / 4]) { const arm = mesh(new THREE.BoxGeometry(5, .18, .22), 0x28483e, 0, .05, 0, drone); arm.rotation.y = angle }
    for (const x of [-1.7, 1.7]) for (const z of [-1.7, 1.7]) {
      mesh(new THREE.CylinderGeometry(.22, .22, .35, 8), 0x344c45, x, .15, z, drone)
      const rotor = mesh(new THREE.BoxGeometry(1.65, .04, .14), 0x172f2a, x, .4, z, drone); rotors.push(rotor)
    }
    mesh(new THREE.SphereGeometry(.3, 8, 6), 0x21352f, 0, -.45, .6, drone)
    marker = mesh(new THREE.TorusGeometry(1.3, .12, 8, 32), 0xf5b951, 0, 0, 0); marker.rotation.x = Math.PI / 2
    updateRoute(); updateTarget(); resetView()
    resize = new ResizeObserver(() => {
      if (!host.value || !renderer) return
      const { width, height } = host.value.getBoundingClientRect()
      if (!width || !height) return
      camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height)
    }); resize.observe(host.value)
    document.addEventListener('visibilitychange', visibilityChange)
    const animate = (time: number) => {
      if (disposed || !renderer) return
      frame = requestAnimationFrame(animate)
      const dt = previousTime ? Math.min((time - previousTime) / 1000, .1) : 0; previousTime = time
      if (props.running && !document.hidden) emit('advance', dt)
      drone.position.set(...props.position); drone.rotation.y = THREE.MathUtils.degToRad(props.heading)
      if (props.running) rotors.forEach((rotor, index) => rotor.rotation.y += dt * (index % 2 ? -65 : 65))
      marker.rotation.z += dt * .4
      if (transition) {
        const t = Math.min(1, (time - transition.start) / 1000), eased = t * t * (3 - 2 * t)
        camera.position.lerpVectors(transition.from, transition.to, eased); controls.target.lerpVectors(transition.targetFrom, transition.target, eased)
        if (t >= 1) { const done = transition; transition = null; done.finish(done.result) }
      } else if (props.cameraMode === 'follow') {
        const position = drone.position.clone(); camera.position.lerp(position.clone().add(new THREE.Vector3(10, 7, 14)), Math.min(1, dt * 3)); controls.target.lerp(position, Math.min(1, dt * 5))
      }
      controls.update(); renderer.render(scene, camera)
    }
    frame = requestAnimationFrame(animate); emit('ready')
  } catch (reason) { error.value = `无法初始化三维场景：${reason instanceof Error ? reason.message : 'WebGL 不可用'}`; emit('error', error.value) }
})
watch(() => props.points, updateRoute)
watch(() => props.targetIndex, updateTarget)
watch(() => props.cameraMode, changeCamera)
onBeforeUnmount(() => {
  disposed = true; cancelAnimationFrame(frame); resize?.disconnect(); transition?.finish(null); transition = null
  document.removeEventListener('visibilitychange', visibilityChange); controls?.dispose()
  const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>()
  scene?.traverse(object => { if (object instanceof THREE.InstancedMesh) object.dispose(); if (object instanceof THREE.DirectionalLight) object.shadow.dispose(); if (object instanceof THREE.Sprite) { object.material.map?.dispose(); materials.add(object.material) } if (object instanceof THREE.Mesh || object instanceof THREE.Line) { geometries.add(object.geometry); for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material) } })
  geometries.forEach(value => value.dispose()); materials.forEach(value => { if (value instanceof THREE.MeshStandardMaterial) value.map?.dispose(); value.dispose() })
  renderer?.domElement.removeEventListener('webglcontextlost', contextLost); renderer?.dispose(); renderer?.domElement.remove(); renderer = undefined
})
function observePlant(index: number, position: Position3) { if (!scene) return 0; scene.updateMatrixWorld(true); return 1 - occlusion(new THREE.Vector3(...position), leafFocus(index)) }
defineExpose({ optimizeView, resetView, observePlant })
</script>

<template>
  <div ref="host" class="scene-host" role="img" aria-label="玉米植株巡航仿真，拖动旋转视角，滚轮缩放">
    <div v-if="error" class="scene-error" role="alert">{{ error }}</div>
  </div>
</template>
<style scoped>
.scene-host{position:absolute;inset:0;overflow:hidden;touch-action:none}.scene-host :deep(canvas){display:block;width:100%;height:100%;outline:none}.scene-error{position:absolute;z-index:3;inset:0;display:grid;place-items:center;background:#e7eeea;color:#8e3636;padding:40px;text-align:center}
</style>
