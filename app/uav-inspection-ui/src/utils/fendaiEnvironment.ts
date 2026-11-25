import * as THREE from 'three'
import { terrainHeight } from './fendaiMotion'
import { createFendaiNearDetail } from './fendaiNearDetail'
import type { GardenProfile } from './fendaiMotion'

const noise = /* glsl */ `
  float hash(vec2 p) {
    vec3 q = fract(vec3(p.xyx) * 0.1031);
    q += dot(q, q.yzx + 33.33);
    return fract((q.x + q.y) * q.z);
  }
  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 u = fract(p);
    u = u * u * (3.0 - 2.0 * u);
    return mix(
      mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
      mix(hash(i + vec2(0.0, 1.0)), hash(i + 1.0), u.x),
      u.y
    );
  }
  float fbm(vec2 p) {
    float n = 0.0;
    float weight = 0.53;
    mat2 rotation = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 5; ++i) {
      n += weight * noise(p);
      p = rotation * p * 2.08 + 9.7;
      weight *= 0.48;
    }
    return n;
  }
`

export function createFendaiEnvironment(profile: GardenProfile) {
  const scene = new THREE.Scene()
  const skyGeometry = new THREE.PlaneGeometry(2, 2)
  const skyMaterial = new THREE.ShaderMaterial({
    depthTest: false,
    depthWrite: false,
    uniforms: {
      time: { value: 0 },
      inverseProjection: { value: new THREE.Matrix4() },
      cameraWorld: { value: new THREE.Matrix4() },
    },
    vertexShader: /* glsl */ `
      uniform mat4 inverseProjection;
      uniform mat4 cameraWorld;
      varying vec3 direction;
      void main() {
        vec4 ray = inverseProjection * vec4(position.xy, 1.0, 1.0);
        direction = mat3(cameraWorld) * ray.xyz;
        gl_Position = vec4(position.xy, 1.0, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float time;
      varying vec3 direction;
      ${noise}
      void main() {
        vec3 view = normalize(direction);
        vec3 sunlight = normalize(vec3(-0.54, 0.30, 0.79));
        float altitude = max(0.0, view.y);
        float sunAngle = max(0.0, dot(view, sunlight));
        vec3 haze = vec3(0.71, 0.83, 0.89);
        vec3 blue = vec3(0.20, 0.49, 0.74);
        vec3 sky = mix(haze, blue, pow(altitude, 0.56));
        sky += vec3(0.22, 0.17, 0.09) * pow(sunAngle, 24.0);
        sky += vec3(0.44, 0.34, 0.17) * pow(sunAngle, 220.0);
        float sun = smoothstep(0.99978, 0.99991, sunAngle);
        sky = mix(sky, vec3(1.0, 0.98, 0.88), sun);
        vec2 cloudPoint = view.xz / (altitude + 0.25) * 2.65 + vec2(time * 0.011, -time * 0.004);
        float cover = fbm(cloudPoint);
        float light = clamp((cover - fbm(cloudPoint - sunlight.xz * 0.16)) * 4.4 + 0.59, 0.0, 1.0);
        float clouds = smoothstep(0.46, 0.65, cover) * smoothstep(0.045, 0.20, altitude);
        vec3 cloud = mix(vec3(0.64, 0.72, 0.80), vec3(1.0, 0.98, 0.91), light);
        gl_FragColor = vec4(mix(sky, cloud, clouds * (1.0 - sun * 0.5)), 1.0);
      }
    `,
  })
  const sky = new THREE.Mesh(skyGeometry, skyMaterial)
  sky.renderOrder = -10
  sky.frustumCulled = false
  scene.add(sky)

  // Place the opaque support surface below the sampled terrain to fill gaps in the splats.
  const radius = 180
  const groundGeometry = new THREE.PlaneGeometry(radius * 2, radius * 2, 144, 144)
  groundGeometry.rotateX(-Math.PI / 2)
  const positions = groundGeometry.attributes.position!
  const { heights, step, origin } = profile
  const maxX = origin[0] + (heights[0]!.length - 1) * step
  const maxZ = origin[1] + (heights.length - 1) * step
  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i)
    const z = positions.getZ(i)
    const outside = Math.max(0, origin[0] - x, x - maxX, origin[1] - z, z - maxZ)
    const blend = THREE.MathUtils.smoothstep(outside, 0, 29)
    const hills = -4 + 4.2 * Math.sin(x * 0.038 + 0.8) * Math.cos(z * 0.04)
    positions.setY(i, THREE.MathUtils.lerp(terrainHeight(profile, x, z) - 0.52, hills, blend))
  }
  const groundMaterial = new THREE.ShaderMaterial({
    depthWrite: false,
    vertexShader: /* glsl */ `
      varying vec3 worldPosition;
      void main() {
        worldPosition = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      varying vec3 worldPosition;
      ${noise}
      void main() {
        vec2 point = worldPosition.xz;
        float vegetation = fbm(point * 0.18) * 0.78 + noise(point * 3.0) * 0.22;
        vec3 grass = mix(vec3(0.15, 0.23, 0.13), vec3(0.38, 0.46, 0.24), vegetation);
        float distanceFog = smoothstep(35.0, 186.0, distance(cameraPosition, worldPosition));
        gl_FragColor = vec4(mix(grass, vec3(0.71, 0.83, 0.89), distanceFog), 1.0);
      }
    `,
  })
  scene.add(new THREE.Mesh(groundGeometry, groundMaterial))

  // The overlay is registered to the red path and sits under the original splats.
  // It supplies a continuous surface where the aerial reconstruction thins at eye level.
  const curve = new THREE.CatmullRomCurve3(profile.path.map(([x, z]) => new THREE.Vector3(x, 0, z)))
  const divisions = 128
  const vertices: number[] = []
  const uv: number[] = []
  const faces: number[] = []
  for (let i = 0; i <= divisions; i++) {
    const t = i / divisions
    const point = curve.getPointAt(t)
    const tangent = curve.getTangentAt(t)
    for (const side of [-1, 1]) {
      const x = point.x + side * 1.78 * tangent.z
      const z = point.z - side * 1.78 * tangent.x
      vertices.push(x, terrainHeight(profile, x, z) - 0.36, z)
      uv.push((side + 1) / 2, t)
    }
    if (i < divisions) {
      const j = i * 2
      faces.push(j, j + 1, j + 2, j + 1, j + 3, j + 2)
    }
  }
  const pathGeometry = new THREE.BufferGeometry()
  pathGeometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
  pathGeometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  pathGeometry.setIndex(faces)
  const pathMaterial = new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    depthWrite: false,
    transparent: true,
    vertexShader: /* glsl */ `
      varying vec2 pathUV;
      varying vec3 pathPoint;
      void main() {
        pathUV = uv;
        pathPoint = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      varying vec2 pathUV;
      varying vec3 pathPoint;
      ${noise}
      void main() {
        float side = abs(pathUV.x - 0.5) * 2.0;
        float detail = noise(pathPoint.xz * 20.0) - 0.5;
        vec3 pavement = vec3(0.79, 0.37, 0.29) + detail * 0.07;
        vec3 curb = vec3(0.77, 0.76, 0.70) + detail * 0.05;
        float edge = smoothstep(0.84, 0.91, side);
        float ends = smoothstep(0.0, 0.035, pathUV.y) * smoothstep(0.0, 0.035, 1.0 - pathUV.y);
        gl_FragColor = vec4(mix(pavement, curb, edge), ends * (1.0 - smoothstep(0.96, 1.0, side)));
      }
    `,
  })
  scene.add(new THREE.Mesh(pathGeometry, pathMaterial))
  const nearDetail = profile.pathDetail && createFendaiNearDetail(profile.pathDetail, noise)

  return {
    scene,
    installSplatDetail(material: THREE.ShaderMaterial) {
      nearDetail?.install(material)
    },
    update(camera: THREE.PerspectiveCamera, seconds: number, groundClearance: number) {
      camera.updateMatrixWorld()
      skyMaterial.uniforms.inverseProjection!.value.copy(camera.projectionMatrixInverse)
      skyMaterial.uniforms.cameraWorld!.value.copy(camera.matrixWorld)
      skyMaterial.uniforms.time!.value = seconds
      nearDetail?.update(camera, groundClearance)
    },
    renderNearDetail(renderer: THREE.WebGLRenderer, camera: THREE.PerspectiveCamera) {
      nearDetail?.render(renderer, camera)
    },
    dispose() {
      skyGeometry.dispose()
      skyMaterial.dispose()
      groundGeometry.dispose()
      groundMaterial.dispose()
      pathGeometry.dispose()
      pathMaterial.dispose()
      nearDetail?.dispose()
    },
  }
}
