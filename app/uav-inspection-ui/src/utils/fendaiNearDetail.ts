import * as THREE from 'three'
import { BENCH_DEPTH, BENCH_WIDTH, pathSectionAt } from './fendaiMotion'
import type { PathDetail } from './fendaiMotion'

function makeRoad(detail: PathDetail, strength: THREE.IUniform<number>, noise: string) {
  const { sections } = detail
  const curve = new THREE.CatmullRomCurve3(sections.map(([x, z, height]) => new THREE.Vector3(x, height, z)))
  const vertices: number[] = []
  const widths: number[] = []
  const uv: number[] = []
  const indices: number[] = []
  const rows = (sections.length - 1) * 8
  let distance = 0
  let previous: THREE.Vector3 | undefined
  for (let i = 0; i <= rows; i++) {
    const t = i / rows
    const section = Math.min(sections.length - 2, Math.floor(t * (sections.length - 1)))
    const local = t * (sections.length - 1) - section
    const point = curve.getPoint(t)
    const tangent = curve.getTangent(t)
    const length = Math.hypot(tangent.x, tangent.z)
    const inner = THREE.MathUtils.lerp(sections[section]![3], sections[section + 1]![3], local)
    const outer = THREE.MathUtils.lerp(sections[section]![4], sections[section + 1]![4], local)
    if (previous) distance += point.distanceTo(previous)
    previous = point
    for (const offset of [-inner, 0, outer]) {
      vertices.push(
        point.x + offset * tangent.z / length,
        point.y - offset * 0.043,
        point.z - offset * tangent.x / length,
      )
      uv.push(offset, distance)
      widths.push(inner, outer)
    }

    if (i < rows) {
      const j = i * 3
      indices.push(j, j + 1, j + 3, j + 1, j + 4, j + 3)
      indices.push(j + 1, j + 2, j + 4, j + 2, j + 5, j + 4)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  geometry.setAttribute('edgeWidths', new THREE.Float32BufferAttribute(widths, 2))
  geometry.setIndex(indices)
  const material = new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
    uniforms: { strength, roadLength: { value: distance } },
    vertexShader: /* glsl */ `
      attribute vec2 edgeWidths;
      varying vec2 road;
      varying vec2 widths;
      varying vec3 worldPoint;
      void main() {
        road = uv;
        widths = edgeWidths;
        worldPoint = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float strength;
      uniform float roadLength;
      varying vec2 road;
      varying vec2 widths;
      varying vec3 worldPoint;
      ${noise}
      void main() {
        bool inner = road.x < 0.0;
        float edge = inner ? widths.x + road.x : widths.y - road.x;
        float kerbWidth = inner ? max(0.68, widths.x - 1.92) : 0.26;
        float division = kerbWidth - edge;
        float curb = smoothstep(-0.018, 0.015, division);
        vec2 point = worldPoint.xz;
        float pixelFootprint = max(length(dFdx(point)), length(dFdy(point)));
        float fine = (noise(point * 83.0) - 0.5) * (1.0 - smoothstep(0.005, 0.028, pixelFootprint));
        float grain = (noise(point * 26.0) - 0.5) * (1.0 - smoothstep(0.02, 0.10, pixelFootprint));
        float wear = noise(point * 1.2) * 0.63 + noise(point * 4.8) * 0.37 - 0.5;
        vec3 pavement = vec3(0.83, 0.41, 0.33) + fine * 0.085 + grain * 0.105 + wear * 0.13;
        pavement *= 1.0 - 0.18 * (1.0 - smoothstep(0.0, 0.15, abs(division)));
        float slabLength = inner ? 0.57 : 0.48;
        float slab = road.y / slabLength + (inner ? 0.0 : 0.38);
        float seamDistance = abs(fract(slab + 0.5) - 0.5) * slabLength;
        float seam = 1.0 - smoothstep(0.004, 0.018 + fwidth(road.y) * 0.35, seamDistance);
        float stone = hash(vec2(floor(slab + 0.5), inner ? 9.0 : 17.0));
        vec3 paver = mix(vec3(0.70, 0.69, 0.65), vec3(0.83, 0.83, 0.79), stone);
        paver += wear * 0.09 + grain * 0.07 + fine * 0.046;
        paver = mix(paver, vec3(0.38, 0.38, 0.34), seam * 0.70);
        float nearView = 1.0 - smoothstep(18.0, 36.0, distance(cameraPosition, worldPoint));
        float ends = smoothstep(0.0, 4.0, road.y) * smoothstep(0.0, 4.0, roadLength - road.y);
        float edgeFade = smoothstep(0.0, 0.15, edge);
        gl_FragColor = vec4(mix(pavement, paver, curb), 0.97 * strength * nearView * ends * edgeFade);
      }
    `,
  })
  return new THREE.Mesh(geometry, material)
}

function makeBenches(detail: PathDetail) {
  const group = new THREE.Group()
  // Shared meshes use the measured center and heading; the wood and iron profile is inferred.
  const box = new THREE.BoxGeometry(1, 1, 1)
  const iron = new THREE.MeshStandardMaterial({ color: 0x302f2c, roughness: 0.83, metalness: 0.18, transparent: true })
  const timber = [
    new THREE.MeshStandardMaterial({ color: 0x9e6343, roughness: 0.88, transparent: true }),
    new THREE.MeshStandardMaterial({ color: 0xae7754, roughness: 0.86, transparent: true }),
  ]
  const shadowGeometry = new THREE.CircleGeometry(1, 24)
  const shadowMaterial = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { opacity: { value: 0 } },
    vertexShader: /* glsl */ `
      varying vec2 shadowUV;
      void main() {
        shadowUV = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float opacity;
      varying vec2 shadowUV;
      void main() {
        float fade = 1.0 - smoothstep(0.15, 0.5, distance(shadowUV, vec2(0.5)));
        gl_FragColor = vec4(0.15, 0.12, 0.09, opacity * fade);
      }
    `,
  })
  for (const [x, z, height, yaw] of detail.benches) {
    const bench = new THREE.Group()
    bench.position.set(x, height, z)
    bench.rotation.y = yaw
    group.add(bench)

    const part = (material: THREE.Material, position: THREE.Vector3Tuple, dimensions: THREE.Vector3Tuple, tilt = 0) => {
      const mesh = new THREE.Mesh(box, material)
      mesh.position.set(...position)
      mesh.scale.set(...dimensions)
      mesh.rotation.x = tilt
      bench.add(mesh)
    }
    for (let i = 0; i < 5; i++) {
      part(timber[i % 2]!, [0, 0.44, -0.17 + i * 0.089], [BENCH_WIDTH, 0.038, 0.077])
    }
    for (let i = 0; i < 4; i++) {
      part(timber[i % 2]!, [0, 0.56 + i * 0.099, -0.22 - i * 0.025], [BENCH_WIDTH, 0.082, 0.035], -0.22)
    }
    for (const end of [-0.68, 0.68]) {
      part(iron, [end, 0.195, -0.17], [0.046, 0.39, 0.055], -0.16)
      part(iron, [end, 0.194, 0.19], [0.046, 0.39, 0.055], 0.14)
      part(iron, [end, 0.4, 0.015], [0.052, 0.04, 0.44])
      part(iron, [end, 0.62, -0.245], [0.036, 0.46, 0.04], -0.23)
      part(iron, [end, 0.58, 0.17], [0.033, 0.28, 0.033])
      part(iron, [end, 0.67, -0.018], [0.045, 0.033, 0.42])
    }
    part(iron, [0, 0.24, -0.02], [1.38, 0.045, 0.05])

    const shadow = new THREE.Mesh(shadowGeometry, shadowMaterial)
    shadow.position.set(0.10, 0.013, 0.02)
    shadow.rotation.x = -Math.PI / 2
    shadow.scale.set(1.22, 0.68, 1)
    shadow.renderOrder = 1
    bench.add(shadow)
  }
  return {
    group,
    update(amount: number) {
      group.visible = amount > 0.001
      for (const material of [iron, ...timber]) material.opacity = amount
      shadowMaterial.uniforms.opacity!.value = amount * 0.36
    },
    dispose() {
      box.dispose()
      iron.dispose()
      timber.forEach(material => material.dispose())
      shadowGeometry.dispose()
      shadowMaterial.dispose()
    },
  }
}

function makeRepairMask(detail: PathDetail) {
  // Red is pavement coverage, green is elevation (0–12 units), blue is bench coverage.
  const border = 4
  const spacing = 0.19
  const minX = Math.min(...detail.sections.map(section => section[0])) - border
  const minZ = Math.min(...detail.sections.map(section => section[1])) - border
  const cols = Math.ceil((Math.max(...detail.sections.map(section => section[0])) + border - minX) / spacing)
  const rows = Math.ceil((Math.max(...detail.sections.map(section => section[1])) + border - minZ) / spacing)
  const pixels = new Uint8Array(cols * rows * 4)
  for (let row = 0; row < rows; row++) {
    const z = minZ + (row + 0.5) * spacing
    for (let col = 0; col < cols; col++) {
      const x = minX + (col + 0.5) * spacing
      const point = pathSectionAt(detail, x, z)
      if (!point) continue
      const at = (row * cols + col) * 4
      const edgeFade = 1 - THREE.MathUtils.smoothstep(point.distance, point.width - 0.18, point.width + 0.12)
      pixels[at] = Math.round(255 * edgeFade * point.fade)
      pixels[at + 1] = Math.round(255 * THREE.MathUtils.clamp(point.height / 12, 0, 1))
      for (const [bx, bz, , yaw] of detail.benches) {
        const dx = x - bx
        const dz = z - bz
        const along = Math.abs(dx * Math.cos(yaw) - dz * Math.sin(yaw))
        const across = Math.abs(dx * Math.sin(yaw) + dz * Math.cos(yaw))
        if (along <= BENCH_WIDTH / 2 + 0.57 && across <= BENCH_DEPTH / 2 + 0.52) {
          pixels[at + 2] = 255
          break
        }
      }
      pixels[at + 3] = 255
    }
  }
  const texture = new THREE.DataTexture(pixels, cols, rows)
  texture.magFilter = THREE.LinearFilter
  texture.minFilter = THREE.LinearFilter
  texture.needsUpdate = true
  return { texture, bounds: new THREE.Vector4(minX, minZ, cols * spacing, rows * spacing) }
}

export function createFendaiNearDetail(detail: PathDetail, noise: string) {
  const scene = new THREE.Scene()
  const strength = { value: 0 }
  const roadMesh = makeRoad(detail, strength, noise)
  roadMesh.renderOrder = 1
  const benches = makeBenches(detail)
  benches.group.visible = false
  const sun = new THREE.DirectionalLight(0xfff1d4, 2.1)
  sun.position.set(-0.54, 0.68, 0.79)
  scene.add(roadMesh, benches.group, sun, new THREE.HemisphereLight(0xdceaff, 0x636846, 1.8))
  const { texture, bounds } = makeRepairMask(detail)
  let installed = false
  return {
    install(material: THREE.ShaderMaterial) {
      // gaussian-splats-3d 0.4.7 projects these two covariance axes just before quad expansion.
      // If that shader changes, the normal splat and backing surfaces still render.
      const marker = 'vec2 basisVector1 = eigenVector1 * splatScale'
      if (!material.vertexShader.includes(marker)) return
      material.uniforms.gardenDetailStrength = strength
      material.uniforms.gardenRepairMask = { value: texture }
      material.uniforms.gardenRepairBounds = { value: bounds }
      material.vertexShader = /* glsl */ `
        uniform float gardenDetailStrength;
        uniform sampler2D gardenRepairMask;
        uniform vec4 gardenRepairBounds;
      ` + material.vertexShader.replace(marker, /* glsl */ `
        if (gardenDetailStrength > 0.001) {
          float viewpointDistance = length(viewCenter.xyz);
          float detail = gardenDetailStrength * (1.0 - smoothstep(18.0, 36.0, viewpointDistance));
          vec2 repairUV = (splatCenter.xz - gardenRepairBounds.xy) / gardenRepairBounds.zw;
          if (detail > 0.001 && all(greaterThanEqual(repairUV, vec2(0.0))) && all(lessThanEqual(repairUV, vec2(1.0)))) {
            vec3 repair = texture2D(gardenRepairMask, repairUV).rgb;
            float aboveSurface = splatCenter.y - repair.g * 12.0;
            float pavement = repair.r * (1.0 - smoothstep(0.35, 1.08, abs(aboveSurface)));
            float ghost = max(repair.b, repair.r) * (1.0 - smoothstep(1.05, 2.1, abs(aboveSurface)));
            float replacement = max(pavement, ghost * (repair.b > 0.1 ? 1.0 : 0.92));
            vColor.a *= 1.0 - replacement * detail;
          }
          float projectedRadius = sqrt8 * sqrt(max(0.0, eigenValue1));
          float anisotropy = sqrt(max(0.0, eigenValue1) / max(0.01, eigenValue2));
          float smear = smoothstep(32.0, 115.0, projectedRadius) * smoothstep(2.7, 9.0, anisotropy);
          vColor.a *= 1.0 - 0.78 * detail * smear * (1.0 - smoothstep(7.0, 17.0, viewpointDistance));
        }
        ${marker}
      `)
      material.needsUpdate = true
      installed = true
    },
    update(camera: THREE.PerspectiveCamera, groundClearance: number) {
      const section = pathSectionAt(detail, camera.position.x, camera.position.z)
      const besideRoad = section ? 1 - THREE.MathUtils.smoothstep(section.distance - section.width, 0.5, 3.2) : 0
      const amount = installed
        ? besideRoad * (1 - THREE.MathUtils.smoothstep(groundClearance, 2.4, 8.5))
        : 0
      strength.value = amount
      roadMesh.visible = amount > 0.001
      benches.update(amount)
    },
    render(renderer: THREE.WebGLRenderer, camera: THREE.PerspectiveCamera) {
      if (!roadMesh.visible) return
      // The splat pass does not write depth. Draw the registered repair last so curb
      // edges stay legible where oblique Gaussian quads extend across the road.
      const autoClear = renderer.autoClear
      renderer.autoClear = false
      renderer.render(scene, camera)
      renderer.autoClear = autoClear
    },
    dispose() {
      roadMesh.material.dispose()
      roadMesh.geometry.dispose()
      benches.dispose()
      texture.dispose()
    },
  }
}
