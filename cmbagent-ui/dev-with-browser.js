#!/usr/bin/env node

const { spawn } = require('child_process');

// Dynamic import for open package
async function openUrl(url) {
  const open = await import('open');
  return open.default(url);
}

// Start Next.js dev server
const nextProcess = spawn('npx', ['next', 'dev'], {
  stdio: ['inherit', 'pipe', 'inherit'],
  shell: true
});

let browserOpened = false;

nextProcess.stdout.on('data', (data) => {
  const output = data.toString();
  process.stdout.write(output);
  
  // Look for the Local URL in the output
  const localMatch = output.match(/- Local:\s+(http:\/\/localhost:\d+)/);
  
  if (localMatch && !browserOpened) {
    const url = localMatch[1];
    console.log(`Opening browser at ${url}`);
    openUrl(url).catch(console.error);
    browserOpened = true;
  }
});

// Handle process termination
process.on('SIGINT', () => {
  nextProcess.kill('SIGINT');
});

process.on('SIGTERM', () => {
  nextProcess.kill('SIGTERM');
});

nextProcess.on('close', (code) => {
  process.exit(code);
});