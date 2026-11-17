"""Ephemeral TEST ONLY TSA; never use its CA as a production trust anchor."""
from pathlib import Path
import subprocess


def run(*args):
    result = subprocess.run(list(args), capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def fixture_tsa(directory: Path):
    ca, key, cert = directory/'ca.pem', directory/'tsa.key', directory/'tsa.pem'
    run('openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(directory/'ca.key'),
        '-out',str(ca),'-days','2','-subj','/CN=FI FIXTURE CA NOT PRODUCTION',
        '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
    run('openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',str(key),
        '-out',str(directory/'tsa.csr'),'-subj','/CN=FI FIXTURE TSA NOT PRODUCTION')
    (directory/'ext.cnf').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,timeStamping\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    run('openssl','x509','-req','-in',str(directory/'tsa.csr'),'-CA',str(ca),'-CAkey',str(directory/'ca.key'),
        '-CAcreateserial','-out',str(cert),'-days','2','-extfile',str(directory/'ext.cnf'))
    (directory/'serial').write_text('01\n')
    config=directory/'tsa.cnf'
    config.write_text(f'''[tsa]
default_tsa=fixture
[fixture]
serial={directory/'serial'}
signer_cert={cert}
certs={ca}
signer_key={key}
signer_digest=sha256
default_policy=1.3.6.1.4.1.55555.1
digests=sha256
accuracy=secs:1
ordering=yes
tsa_name=yes
ess_cert_id_chain=no
ess_cert_id_alg=sha256
''')
    return ca.read_bytes(), config


def timestamp(config, query):
    directory=config.parent
    (directory/'q.tsq').write_bytes(query)
    run('openssl','ts','-reply','-config',str(config),'-queryfile',str(directory/'q.tsq'),'-out',str(directory/'r.tsr'))
    return (directory/'r.tsr').read_bytes()
