"""Ephemeral TEST ONLY TSA; never use its CA as a production trust anchor."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import tempfile


def config_path(path):
    # OpenSSL treats backslashes and variable references in its config as escapes.
    return '"' + path.as_posix().replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


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
serial={config_path(directory/'serial')}
signer_cert={config_path(cert)}
certs={config_path(ca)}
signer_key={config_path(key)}
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


def fixture_crl(directory: Path, *, issuer='ca', certificates=(), revoked=(),
                last_update=None, next_update=None) -> bytes:
    """Issue a real, local TEST ONLY CRL without changing the shared TSA fixture."""
    directory = directory.resolve()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    last_update = last_update if last_update is not None else now - timedelta(minutes=1)
    next_update = next_update if next_update is not None else now + timedelta(days=1)
    for value in (last_update, next_update):
        if value.tzinfo is None:
            raise ValueError('fixture CRL times require a timezone')
    with tempfile.TemporaryDirectory(prefix='crl-', dir=directory) as tmp:
        work = Path(tmp)
        (work/'index').write_text('')
        (work/'serial').write_text('01\n')
        (work/'crlnumber').write_text('01\n')
        config = work/'ca.cnf'
        config.write_text(f'''[ca]
default_ca=fixture
[fixture]
database={config_path(work/'index')}
serial={config_path(work/'serial')}
crlnumber={config_path(work/'crlnumber')}
new_certs_dir={config_path(work)}
certificate={config_path(directory/(issuer+'.pem'))}
private_key={config_path(directory/(issuer+'.key'))}
default_md=sha256
default_days=2
default_crl_days=1
policy=fixture_policy
unique_subject=no
crl_extensions=fixture_crl
[fixture_policy]
commonName=supplied
[fixture_crl]
authorityKeyIdentifier=keyid:always
''')
        entries = dict.fromkeys(Path(cert).resolve() for cert in (*certificates, *revoked))
        for cert in entries:
            run('openssl', 'ca', '-batch', '-config', str(config), '-valid', str(cert))
        for cert in revoked:
            run('openssl', 'ca', '-batch', '-config', str(config), '-revoke', str(Path(cert).resolve()),
                '-crl_reason', 'keyCompromise')
        output = work/'crl.pem'
        run('openssl', 'ca', '-batch', '-config', str(config), '-gencrl',
            '-crl_lastupdate', last_update.astimezone(timezone.utc).strftime('%Y%m%d%H%M%SZ'),
            '-crl_nextupdate', next_update.astimezone(timezone.utc).strftime('%Y%m%d%H%M%SZ'),
            '-out', str(output))
        return output.read_bytes()


def fixture_intermediate_tsa(directory: Path):
    """A separate root -> intermediate -> TSA TEST ONLY chain for full-chain tests."""
    ca, config = fixture_tsa(directory)
    run('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(directory/'intermediate.key'), '-out', str(directory/'intermediate.csr'),
        '-subj', '/CN=FI FIXTURE INTERMEDIATE NOT PRODUCTION')
    extensions = directory/'intermediate.ext'
    extensions.write_text('basicConstraints=critical,CA:TRUE,pathlen:0\n'
                          'keyUsage=critical,keyCertSign,cRLSign\n'
                          'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    run('openssl', 'x509', '-req', '-in', str(directory/'intermediate.csr'),
        '-CA', str(directory/'ca.pem'), '-CAkey', str(directory/'ca.key'), '-CAcreateserial',
        '-out', str(directory/'intermediate.pem'), '-days', '2', '-extfile', str(extensions))
    run('openssl', 'x509', '-req', '-in', str(directory/'tsa.csr'),
        '-CA', str(directory/'intermediate.pem'), '-CAkey', str(directory/'intermediate.key'),
        '-CAcreateserial', '-out', str(directory/'tsa.pem'), '-days', '2',
        '-extfile', str(directory/'ext.cnf'))
    chain = directory/'chain.pem'
    chain.write_bytes((directory/'intermediate.pem').read_bytes() + ca)
    config.write_text(config.read_text().replace(f'certs={config_path(directory/"ca.pem")}\n',
                                                f'certs={config_path(chain)}\n'))
    return ca, config
