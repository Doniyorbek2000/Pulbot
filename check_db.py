import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('169.58.165.15', 22, 'root', '949392250Adm12')

script = """
cd /opt/pulbot
PYTHONPATH=. .venv/bin/python -c "
import asyncio, aiosqlite

async def main():
    async with aiosqlite.connect('/opt/pulbot/pulbot.db') as db:
        async with db.execute('SELECT id, target_type, owner_id, user_id, expires_at FROM active_permissions') as cur:
            print('ACTIVE PERMISSIONS:', await cur.fetchall())
            
        async with db.execute('SELECT id, username, first_name, business_connection_id, business_enabled FROM users') as cur:
            print('USERS:', await cur.fetchall())
            
        async with db.execute('SELECT user_id, mode, price_mxtr FROM inbox_settings') as cur:
            print('INBOX SETTINGS:', await cur.fetchall())

asyncio.run(main())
"
"""
stdin, stdout, stderr = ssh.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
print(stderr.read().decode('utf-8', errors='replace'))
ssh.close()
