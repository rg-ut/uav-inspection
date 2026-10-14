declare module '@mkkellogg/gaussian-splats-3d' {
  export const LogLevel: { None: number }

  export const RenderMode: { Always: number }

  export const SceneFormat: { KSplat: number }

  export class Viewer {
    constructor(options: Record<string, unknown>)
    currentFPS: number | null
    sortRunning: boolean
    splatRenderCount: number
    splatSortCount: number
    addSplatScene(path: string, options?: Record<string, unknown>): {
      promise: Promise<void>
      abort(reason?: string): void
    }
    runSplatSort(force?: boolean, forceSortAll?: boolean): Promise<boolean>
    update(): void
    render(): void
    dispose(): Promise<void>
  }
}
