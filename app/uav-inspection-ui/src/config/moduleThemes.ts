export type ModuleThemeKey =
  | 'telecom'
  | 'power'
  | 'roof'
  | 'wall'
  | 'pest'
  | 'pruning'
  | 'height'
  | 'area'
  | 'roseDigital'
  | 'roseYield'
  | 'roseFlow'
  | 'plate'
  | 'coverage'
  | 'fendai'

export interface ModuleTheme {
  accent: string
  badge: string
  card: string
  gradient: string
  hover: string
  icon: string
  ring: string
  soft: string
  text: string
}

export const moduleThemes: Record<ModuleThemeKey, ModuleTheme> = {
  // 通信 / 信号：科技蓝
  telecom: {
    accent: 'bg-blue-600',
    badge: 'border-blue-200 bg-blue-50 text-blue-700',
    card: 'bg-blue-50/75 border-blue-200/80 shadow-blue-500/10',
    gradient: 'from-blue-500 to-indigo-500',
    hover: 'hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700',
    icon: 'text-blue-600',
    ring: 'ring-blue-200',
    soft: 'bg-blue-50',
    text: 'text-blue-700',
  },
  // 电力 / 线路隐患：警示琥珀
  power: {
    accent: 'bg-amber-500',
    badge: 'border-amber-200 bg-amber-50 text-amber-700',
    card: 'bg-amber-50/75 border-amber-200/80 shadow-amber-500/10',
    gradient: 'from-amber-400 to-orange-400',
    hover: 'hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700',
    icon: 'text-amber-600',
    ring: 'ring-amber-200',
    soft: 'bg-amber-50',
    text: 'text-amber-700',
  },
  // 屋面 / 防水：建筑青
  roof: {
    accent: 'bg-cyan-500',
    badge: 'border-cyan-200 bg-cyan-50 text-cyan-700',
    card: 'bg-cyan-50/75 border-cyan-200/80 shadow-cyan-500/10',
    gradient: 'from-cyan-400 to-sky-400',
    hover: 'hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700',
    icon: 'text-cyan-600',
    ring: 'ring-cyan-200',
    soft: 'bg-cyan-50',
    text: 'text-cyan-700',
  },
  // 外墙 / 裂缝渗水：青绿
  wall: {
    accent: 'bg-teal-500',
    badge: 'border-teal-200 bg-teal-50 text-teal-700',
    card: 'bg-teal-50/75 border-teal-200/80 shadow-teal-500/10',
    gradient: 'from-teal-500 to-emerald-500',
    hover: 'hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700',
    icon: 'text-teal-600',
    ring: 'ring-teal-200',
    soft: 'bg-teal-50',
    text: 'text-teal-700',
  },
  // 病虫害 / 异常：病害玫红
  pest: {
    accent: 'bg-rose-500',
    badge: 'border-rose-200 bg-rose-50 text-rose-700',
    card: 'bg-rose-50/75 border-rose-200/80 shadow-rose-500/10',
    gradient: 'from-rose-500 to-red-500',
    hover: 'hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700',
    icon: 'text-rose-600',
    ring: 'ring-rose-200',
    soft: 'bg-rose-50',
    text: 'text-rose-700',
  },
  // 乔木修剪 / 植被养护：翠绿
  pruning: {
    accent: 'bg-emerald-500',
    badge: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    card: 'bg-emerald-50/75 border-emerald-200/80 shadow-emerald-500/10',
    gradient: 'from-emerald-500 to-green-400',
    hover: 'hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700',
    icon: 'text-emerald-600',
    ring: 'ring-emerald-200',
    soft: 'bg-emerald-50',
    text: 'text-emerald-700',
  },
  // 点云 / 高度测量：测量紫
  height: {
    accent: 'bg-violet-500',
    badge: 'border-violet-200 bg-violet-50 text-violet-700',
    card: 'bg-violet-50/75 border-violet-200/80 shadow-violet-500/10',
    gradient: 'from-violet-500 to-purple-500',
    hover: 'hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700',
    icon: 'text-violet-600',
    ring: 'ring-violet-200',
    soft: 'bg-violet-50',
    text: 'text-violet-700',
  },
  // 绿化 / 草坪面积：青柠
  area: {
    accent: 'bg-lime-500',
    badge: 'border-lime-200 bg-lime-50 text-lime-700',
    card: 'bg-lime-50/75 border-lime-200/80 shadow-lime-500/10',
    gradient: 'from-lime-500 to-green-500',
    hover: 'hover:border-lime-300 hover:bg-lime-50 hover:text-lime-700',
    icon: 'text-lime-600',
    ring: 'ring-lime-200',
    soft: 'bg-lime-50',
    text: 'text-lime-700',
  },
  // 数字玫瑰园：玫瑰洋红
  roseDigital: {
    accent: 'bg-fuchsia-500',
    badge: 'border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700',
    card: 'bg-fuchsia-50/75 border-fuchsia-200/80 shadow-fuchsia-500/10',
    gradient: 'from-fuchsia-500 to-pink-500',
    hover: 'hover:border-fuchsia-300 hover:bg-fuchsia-50 hover:text-fuchsia-700',
    icon: 'text-fuchsia-600',
    ring: 'ring-fuchsia-200',
    soft: 'bg-fuchsia-50',
    text: 'text-fuchsia-700',
  },
  // 玫瑰产量预测：柔粉
  roseYield: {
    accent: 'bg-pink-500',
    badge: 'border-pink-200 bg-pink-50 text-pink-700',
    card: 'bg-pink-50/75 border-pink-200/80 shadow-pink-500/10',
    gradient: 'from-pink-500 to-rose-500',
    hover: 'hover:border-pink-300 hover:bg-pink-50 hover:text-pink-700',
    icon: 'text-pink-600',
    ring: 'ring-pink-200',
    soft: 'bg-pink-50',
    text: 'text-pink-700',
  },
  // 热力客流监测：热力橙（由天蓝改为热力图暖色）
  roseFlow: {
    accent: 'bg-orange-500',
    badge: 'border-orange-200 bg-orange-50 text-orange-700',
    card: 'bg-orange-50/75 border-orange-200/80 shadow-orange-500/10',
    gradient: 'from-orange-400 to-amber-400',
    hover: 'hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700',
    icon: 'text-orange-600',
    ring: 'ring-orange-200',
    soft: 'bg-orange-50',
    text: 'text-orange-700',
  },
  // 车辆违停巡检：违章警示红
  plate: {
    accent: 'bg-red-500',
    badge: 'border-red-200 bg-red-50 text-red-700',
    card: 'bg-red-50/75 border-red-200/80 shadow-red-500/10',
    gradient: 'from-red-500 to-rose-500',
    hover: 'hover:border-red-300 hover:bg-red-50 hover:text-red-700',
    icon: 'text-red-600',
    ring: 'ring-red-200',
    soft: 'bg-red-50',
    text: 'text-red-700',
  },
  // 车辆覆盖率检测：扫描 / 视点测量靛蓝
  coverage: {
    accent: 'bg-indigo-500',
    badge: 'border-indigo-200 bg-indigo-50 text-indigo-700',
    card: 'bg-indigo-50/75 border-indigo-200/80 shadow-indigo-500/10',
    gradient: 'from-indigo-500 to-blue-500',
    hover: 'hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700',
    icon: 'text-indigo-600',
    ring: 'ring-indigo-200',
    soft: 'bg-indigo-50',
    text: 'text-indigo-700',
  },
  // 粉黛花三维漫游：粉雾紫
  fendai: {
    accent: 'bg-purple-500',
    badge: 'border-purple-200 bg-purple-50 text-purple-700',
    card: 'bg-purple-50/75 border-purple-200/80 shadow-purple-500/10',
    gradient: 'from-purple-400 to-pink-400',
    hover: 'hover:border-purple-300 hover:bg-purple-50 hover:text-purple-700',
    icon: 'text-purple-600',
    ring: 'ring-purple-200',
    soft: 'bg-purple-50',
    text: 'text-purple-700',
  },
}
