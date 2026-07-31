/// <reference types="vite/client" />

// Vue SFC module declaration
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// unplugin-vue-components subpath imports (no types shipped)
declare module 'unplugin-vue-components/resolvers' {
  export const NaiveUiResolver: any
  const _default: any
  export default _default
}

declare module 'unplugin-vue-components/vite' {
  import type { Plugin } from 'vite'
  function Components(options?: any): Plugin
  export default Components
}

declare module 'unplugin-vue-components' {
  function createUnplugin(options?: any): any
  export default createUnplugin
}