import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import svelte from 'eslint-plugin-svelte';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  {
    ignores: ['dist/', 'dev-dist/', 'node_modules/', 'coverage/', 'public/sw-custom.js'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs['flat/recommended'],
  prettier,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
    },
    rules: {
      'no-undef': 'off', // TypeScript (and TS inside Svelte) owns this check
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'svelte/no-unused-svelte-ignore': 'error',
    },
  },
  {
    files: ['**/*.svelte'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.svelte'],
      },
    },
  },
);
