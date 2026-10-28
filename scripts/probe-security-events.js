// Run through Juice Shop's Node.js executable, not on the macOS host.
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

async function main() {
  const directories = [];
  const server = net.createServer(socket => socket.end());
  try {
    const sensitive = fs.mkdtempSync('/tmp/runtime-visibility-sensitive-');
    directories.push(sensitive);
    const control = fs.mkdtempSync('/tmp/runtime-visibility-control-');
    directories.push(control);
    const sensitivePath = path.join(sensitive, 'credentials.txt');
    const controlPath = path.join(control, 'ordinary.txt');
    fs.writeFileSync(sensitivePath, 'fake-lab-credential\n', { mode: 0o600 });
    fs.writeFileSync(controlPath, 'ordinary-lab-data\n', { mode: 0o600 });
    fs.readFileSync(sensitivePath);
    fs.readFileSync(controlPath);

    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const port = server.address().port;
    await new Promise((resolve, reject) => {
      const client = net.createConnection({ host: '127.0.0.1', port });
      client.once('error', reject);
      client.once('close', resolve);
      client.setTimeout(3000, () => client.destroy(new Error('TCP probe timed out')));
    });
    console.log(JSON.stringify({
      container_pid: process.pid,
      executable: process.execPath,
      sensitive_path: sensitivePath,
      excluded_control_path: controlPath,
      destination_ip: '127.0.0.1',
      destination_port: port,
      application_connection_succeeded: true,
    }, null, 2));
  } finally {
    if (server.listening) {
      await new Promise(resolve => server.close(resolve));
    }
    for (const directory of directories) {
      fs.rmSync(directory, { recursive: true });
    }
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
