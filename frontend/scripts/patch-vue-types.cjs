/**
 * postinstall 脚本 — 修复 vue-router v5 和 @vue-flow/core 类型声明缺失。
 *
 * vue-router@5.1.0:
 *   package.json 指向 dist/vue-router.d.ts，但实际类型在 hash 命名的
 *   dist/index-BQLwgiyK.d.ts 中，且使用了 minified 导出名（useRoute as x）。
 *   本脚本解析 barrel export，生成带正确导出名的桥接文件。
 *
 * @vue-flow/core@1.48.2:
 *   dist 根目录没有 index.d.ts，本脚本收集子目录类型文件并生成 index.d.ts。
 */
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

// ─── vue-router 修复 ───

function patchVueRouter() {
  const distDir = path.join(root, 'node_modules', 'vue-router', 'dist');
  if (!fs.existsSync(distDir)) return;

  // 找主类型文件
  const files = fs.readdirSync(distDir);
  const mainDts = files.find(f => f.startsWith('index-') && f.endsWith('.d.ts'));
  if (!mainDts) {
    console.warn('[postinstall] vue-router: 找不到主 .d.ts 文件');
    return;
  }

  const srcPath = path.join(distDir, mainDts);
  const bridgePath = path.join(distDir, 'vue-router.d.ts');

  // 读取类型文件，解析 barrel export
  const content = fs.readFileSync(srcPath, 'utf-8');
  const exportMatch = content.match(/export \{ ([^}]+)\}[;]?\s*$/m);
  if (!exportMatch) {
    console.warn('[postinstall] vue-router: 找不到 barrel export');
    return;
  }

  // 解析 "Name as MinName" 对
  const parts = exportMatch[1].split(', ');
  const exports = [];
  for (const part of parts) {
    // 格式: "OriginalName as MinName"
    // 或: "OriginalName" (没有 rename)
    const asMatch = part.match(/^(.+?)\s+as\s+(\w+)$/);
    if (asMatch) {
      exports.push({ original: asMatch[1], minified: asMatch[2] });
    } else {
      exports.push({ original: part, minified: part });
    }
  }

  // 生成桥接文件 — 使用 import { minName as origName } 然后 export { origName }
  // 处理原始名称重复的情况：追加 _2, _3 等后缀
  const seen = new Map();
  const importParts = [];
  const exportNames = [];
  for (const exp of exports) {
    if (exp.original !== exp.minified) {
      let name = exp.original;
      const count = seen.get(name) || 0;
      if (count > 0) {
        name = `${name}_${count + 1}`;
      }
      seen.set(exp.original, count + 1);
      importParts.push(`${exp.minified} as ${name}`);
      exportNames.push(name);
    }
  }

  const bridgeLines = [
    '/**',
    ' * vue-router v5 — auto-generated type bridge (postinstall)',
    ' * 从 minified 导出名重新导出为正确名称',
    ' */',
    `import { ${importParts.join(', ')} } from './${mainDts.replace(/\\.d\\.ts$/, '')}';`,
    '',
    `export { ${exportNames.join(', ')} };`,
    '',
  ];
  fs.writeFileSync(bridgePath, bridgeLines.join('\n'));

  // 更新 package.json 的 types 指针
  const pkgPath = path.join(root, 'node_modules', 'vue-router', 'package.json');
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
  const needUpdate = pkg.types !== './dist/vue-router.d.ts' ||
    pkg.exports?.['.']?.types !== './dist/vue-router.d.ts';

  if (needUpdate) {
    pkg.types = './dist/vue-router.d.ts';
    if (pkg.exports?.['.']) {
      pkg.exports['.'].types = './dist/vue-router.d.ts';
    }
    fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
  }

  console.log(`[postinstall] vue-router: 生成桥接文件 vue-router.d.ts (${exports.length} 个导出)`);
}

// ─── @vue-flow/core 修复 ───

function patchVueFlowCore() {
  const distDir = path.join(root, 'node_modules', '@vue-flow', 'core', 'dist');
  if (!fs.existsSync(distDir)) return;

  // 先生成缺失的子模块类型文件（确保下面的收集步骤能找到它们）
  generateVueFlowMissingTypes(distDir);

  const targetIndex = path.join(distDir, 'index.d.ts');

  // 收集所有有意义的 .d.ts 文件
  const dtsFiles = [];
  function walkDir(dir, prefix = '') {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      const relPath = prefix + entry.name;
      if (entry.isDirectory()) {
        walkDir(fullPath, relPath + '/');
      } else if (entry.name.endsWith('.d.ts') && !entry.name.endsWith('.vue.d.ts')) {
        try {
          const content = fs.readFileSync(fullPath, 'utf-8');
          if (content.includes('export') && !content.includes('declare module')) {
            dtsFiles.push({
              rel: relPath.replace(/\\/g, '/').replace(/\.d\.ts$/, ''),
              depth: relPath.split('/').length,
            });
          }
        } catch {}
      }
    }
  }
  walkDir(distDir);

  if (dtsFiles.length === 0) {
    console.warn('[postinstall] @vue-flow/core: 找不到 .d.ts 文件');
    return;
  }

  dtsFiles.sort((a, b) => a.depth - b.depth);

  const lines = ['/**', ' * @vue-flow/core — auto-generated type bridge (postinstall)', ' */'];
  for (const f of dtsFiles) {
    lines.push(`export * from './${f.rel}';`);
  }
  lines.push('');
  fs.writeFileSync(targetIndex, lines.join('\n'));

  // 更新 package.json
  const pkgPath = path.join(root, 'node_modules', '@vue-flow', 'core', 'package.json');
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
  const needUpdate = pkg.types !== './dist/index.d.ts' ||
    pkg.exports?.['.']?.types !== './dist/index.d.ts';

  if (needUpdate) {
    pkg.types = './dist/index.d.ts';
    if (pkg.exports?.['.']) {
      pkg.exports['.'].types = './dist/index.d.ts';
    }
    fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
  }

  console.log(`[postinstall] @vue-flow/core: 生成 index.d.ts (${dtsFiles.length} 个源文件)`);
}

function generateVueFlowMissingTypes(distDir) {
  // types/node.d.ts — 被多处引用但包中缺失
  const nodeDts = path.join(distDir, 'types', 'node.d.ts');
  if (!fs.existsSync(nodeDts)) {
    fs.writeFileSync(nodeDts, `/**
 * @vue-flow/core — auto-generated node types (postinstall)
 * 原始包缺失此文件，根据引用关系补全
 */
export type NodeData = any;

export interface Node<
  T = NodeData,
  E extends Record<string, any> = any,
> {
  id: string;
  position: { x: number; y: number };
  data: T;
  type?: string;
  label?: string | any;
  style?: Record<string, any>;
  hidden?: boolean;
  selected?: boolean;
  dragging?: boolean;
  connectable?: boolean;
  selectable?: boolean;
  deletable?: boolean;
  draggable?: boolean;
  parentNode?: string;
  extent?: CoordinateExtent;
  expanded?: boolean;
  zIndex?: number;
  ariaLabel?: string;
  focusable?: boolean;
  TabIndex?: number;
  [key: string]: any;
}

export type GraphNode<
  T = NodeData,
  E extends Record<string, any> = any,
> = Node<T, E> & {
  /** 内部使用 */
  selected: boolean;
  dragging: boolean;
  resizing: boolean;
  positionAbsolute: { x: number; y: number };
  width?: number;
  height?: number;
  handles: NodeHandleBounds;
  [key: string]: any;
};

export interface NodeProps<T = NodeData, E extends Record<string, any> = any> {
  id: string;
  data: T;
  type: string;
  selected: boolean;
  dragging: boolean;
  connectable: boolean;
  position: { x: number; y: number };
  dimensions: { width: number; height: number };
  isValidTargetPos?: (connection: any) => boolean;
  isValidSourcePos?: (connection: any) => boolean;
  parentNode?: string;
  zIndex: number;
  dragHandle?: string;
  targetPosition?: 'top' | 'right' | 'bottom' | 'left';
  sourcePosition?: 'top' | 'right' | 'bottom' | 'left';
  onNodesChange?: any;
  onNodeClick?: any;
  onNodeDoubleClick?: any;
  onNodeMouseEnter?: any;
  onNodeMouseLeave?: any;
  onNodeMouseMove?: any;
  onNodeMouseDown?: any;
  onNodeMouseUp?: any;
  onNodeContextMenu?: any;
  onNodeDragStart?: any;
  onNodeDrag?: any;
  onNodeDragStop?: any;
  onNodeResizeStart?: any;
  onNodeResize?: any;
  onNodeResizeStop?: any;
  onConnect?: any;
  onConnectStart?: any;
  onConnectEnd?: any;
  edges?: any[];
  [key: string]: any;
}

export type CoordinateExtent = [[number, number], [number, number]] | [number, number][];
export type CoordinateExtentRange = { range: CoordinateExtent; origin: [number, number] };

export interface NodeHandleBounds {
  source?: HandleBounds[];
  target?: HandleBounds[];
}

export interface HandleBounds {
  id: string | null;
  position: { x: number; y: number };
  width: number;
  height: number;
}

export type DefaultNode<T = NodeData> = Node<T>;
`);
    console.log('[postinstall] @vue-flow/core: 补全 types/node.d.ts');
  }

  // types/zoom.d.ts — 被多处引用但包中缺失
  const zoomDts = path.join(distDir, 'types', 'zoom.d.ts');
  if (!fs.existsSync(zoomDts)) {
    fs.writeFileSync(zoomDts, `/**
 * @vue-flow/core — auto-generated zoom types (postinstall)
 */
export interface ViewportTransform {
  x: number;
  y: number;
  zoom: number;
}

export enum PanOnScrollMode {
  Free = 'free',
  Vertical = 'vertical',
  Horizontal = 'horizontal',
}
`);
    console.log('[postinstall] @vue-flow/core: 补全 types/zoom.d.ts');
  }

  // composables/useVueFlow.d.ts — 缺失的 composable 类型
  const useVueFlowDts = path.join(distDir, 'composables', 'useVueFlow.d.ts');
  if (!fs.existsSync(useVueFlowDts)) {
    fs.writeFileSync(useVueFlowDts, `/**
 * @vue-flow/core — auto-generated useVueFlow composable (postinstall)
 */
import type { Ref, ComputedRef } from 'vue';

// 前向引用类型定义（避免循环导入）
interface Node {
  id: string;
  position: { x: number; y: number };
  data: any;
  type?: string;
  [key: string]: any;
}

interface Edge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  data?: any;
  type?: string;
  [key: string]: any;
}

interface Connection {
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

interface ViewportTransform {
  x: number;
  y: number;
  zoom: number;
}

export interface UseVueFlowReturn {
  // 状态
  nodes: Ref<Node[]>;
  edges: Ref<Edge[]>;
  viewport: Ref<ViewportTransform>;
  selectedNodes: ComputedRef<Node[]>;
  selectedEdges: ComputedRef<Edge[]>;
  getNodes: () => Node[];
  getEdges: () => Edge[];
  getSelectedNodes: () => Node[];
  getSelectedEdges: () => Edge[];
  getNode: (id: string) => Node | undefined;
  getEdge: (id: string) => Edge | undefined;
  findNode: (id: string) => Node | undefined;
  findEdge: (id: string) => Edge | undefined;
  findNodes: (nodeIds: string[]) => Node[];
  findEdges: (edgeIds: string[]) => Edge[];
  // 事件处理
  onNodeClick: (handler: (event: any) => void) => void;
  onNodeDoubleClick: (handler: (event: any) => void) => void;
  onNodeMouseEnter: (handler: (event: any) => void) => void;
  onNodeMouseLeave: (handler: (event: any) => void) => void;
  onNodeMouseMove: (handler: (event: any) => void) => void;
  onNodeMouseDown: (handler: (event: any) => void) => void;
  onNodeMouseUp: (handler: (event: any) => void) => void;
  onNodeContextMenu: (handler: (event: any) => void) => void;
  onNodeDragStart: (handler: (event: any) => void) => void;
  onNodeDrag: (handler: (event: any) => void) => void;
  onNodeDragStop: (handler: (event: any) => void) => void;
  onNodeResizeStart: (handler: (event: any) => void) => void;
  onNodeResize: (handler: (event: any) => void) => void;
  onNodeResizeStop: (handler: (event: any) => void) => void;
  onEdgeClick: (handler: (event: any) => void) => void;
  onEdgeDoubleClick: (handler: (event: any) => void) => void;
  onEdgeMouseEnter: (handler: (event: any) => void) => void;
  onEdgeMouseLeave: (handler: (event: any) => void) => void;
  onEdgeMouseMove: (handler: (event: any) => void) => void;
  onEdgeMouseDown: (handler: (event: any) => void) => void;
  onEdgeMouseUp: (handler: (event: any) => void) => void;
  onEdgeContextMenu: (handler: (event: any) => void) => void;
  onEdgeUpdateStart: (handler: (event: any) => void) => void;
  onEdgeUpdate: (handler: (event: any) => void) => void;
  onEdgeUpdateEnd: (handler: (event: any) => void) => void;
  onConnect: (handler: (connection: Connection) => void) => void;
  onConnectStart: (handler: (event: any) => void) => void;
  onConnectEnd: (handler: (event: any) => void) => void;
  onPaneClick: (handler: (event: any) => void) => void;
  onPaneContextMenu: (handler: (event: any) => void) => void;
  onPaneScroll: (handler: (event: any) => void) => void;
  onPaneMove: (handler: (event: any) => void) => void;
  // 操作方法
  addNodes: (nodes: Node | Node[]) => void;
  addEdges: (edges: Edge | Edge[] | Connection | Connection[]) => void;
  setNodes: (nodes: Node | Node[]) => void;
  setEdges: (edges: Edge | Edge[]) => void;
  updateNodeData: (id: string, data: any) => void;
  updateNodeDataBy: (id: string, dataUpdate: (data: any) => any) => void;
  removeNodes: (nodeIds: string | string[]) => void;
  removeEdges: (edgeIds: string | string[]) => void;
  findNode: (id: string) => Node | undefined;
  findEdge: (id: string) => Edge | undefined;
  fitView: (options?: any) => void;
  fitBounds: (bounds: any, options?: any) => void;
  setViewport: (viewport: ViewportTransform) => void;
  getViewport: () => ViewportTransform;
  zoomIn: (options?: any) => void;
  zoomOut: (options?: any) => void;
  zoomTo: (zoomLevel: number, options?: any) => void;
  getSelectedNodes: () => Node[];
  getSelectedEdges: () => Edge[];
  getSelectedElements: () => any[];
  getElements: () => any[];
  // 其他
  vueFlowRef: Ref<HTMLElement | null>;
  onPaneReady: (handler: (instance: any) => void) => void;
  [key: string]: any;
}

export declare function useVueFlow(
  idOrOptions?: string | { id?: string; [key: string]: any },
): UseVueFlowReturn;
`);
    console.log('[postinstall] @vue-flow/core: 补全 composables/useVueFlow.d.ts');
  }

  // types/index.d.ts — 主类型入口
  const typesIndexDts = path.join(distDir, 'types', 'index.d.ts');
  if (!fs.existsSync(typesIndexDts)) {
    fs.writeFileSync(typesIndexDts, `/**
 * @vue-flow/core — auto-generated types index (postinstall)
 */
export * from './flow';
export * from './node';
export * from './edge';
export * from './connection';
export * from './handle';
export * from './hooks';
export * from './changes';
export * from './components';
export * from './zoom';
`);
    console.log('[postinstall] @vue-flow/core: 补全 types/index.d.ts');
  }
}

// 执行
try {
  patchVueRouter();
} catch (e) {
  console.warn('[postinstall] vue-router patch 失败（非致命）:', e.message);
}

try {
  patchVueFlowCore();
} catch (e) {
  console.warn('[postinstall] @vue-flow/core patch 失败（非致命）:', e.message);
}