import { defineConfig } from 'vite';
import { resolve } from 'path';
import { copyFileSync, mkdirSync, existsSync } from 'fs';

export default defineConfig({
  root: '.',
  publicDir: 'public',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
      },
    },
  },
  server: {
    port: 5173,
    host: true,
  },
  plugins: [
    {
      name: 'copy-vendor',
      closeBundle() {
        const vendorSrc = resolve(__dirname, 'vendor');
        const vendorDest = resolve(__dirname, 'dist/vendor');
        if (existsSync(vendorSrc)) {
          mkdirSync(vendorDest, { recursive: true });
          copyVendor(vendorSrc, vendorDest);
        }
      },
    },
  ],
});

function copyVendor(src: string, dest: string) {
  const fs = require('fs');
  const path = require('path');
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      mkdirSync(destPath, { recursive: true });
      copyVendor(srcPath, destPath);
    } else {
      copyFileSync(srcPath, destPath);
    }
  }
}