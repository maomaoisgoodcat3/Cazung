// `npm start` helper: VS Code terminals set ELECTRON_RUN_AS_NODE=1, which turns Electron into plain
// Node and breaks the app. Launch the Electron binary with that variable removed.
const { spawn } = require('child_process');
const electron = require('electron'); // in plain Node this is the path to the binary

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, ['.'], { cwd: __dirname, env, stdio: 'inherit' });
child.on('exit', code => process.exit(code ?? 0));
