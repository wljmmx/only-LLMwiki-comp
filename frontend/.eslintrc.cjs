/* eslint-env node */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  extends: [
    'eslint:recommended',
    'plugin:vue/vue3-recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:prettier/recommended',
  ],
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  rules: {
    // Prettier 接管格式化
    'prettier/prettier': 'warn',

    // TypeScript 宽松规则（渐进式启用）
    '@typescript-eslint/no-explicit-any': 'warn',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    '@typescript-eslint/no-empty-function': 'off',
    '@typescript-eslint/ban-ts-comment': 'off',

    // Vue 宽松规则
    'vue/multi-word-component-names': 'off',
    'vue/require-default-prop': 'off',
    'vue/attribute-hyphenation': 'off',
    'vue/v-on-event-hyphenation': 'off',

    // 通用规则
    'no-console': ['warn', { allow: ['warn', 'error'] }],
    'no-debugger': 'warn',
  },
  ignorePatterns: ['dist/', 'node_modules/', '*.config.ts', '*.config.js'],
}
