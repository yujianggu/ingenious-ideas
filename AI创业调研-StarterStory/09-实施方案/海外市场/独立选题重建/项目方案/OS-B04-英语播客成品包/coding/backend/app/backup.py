"""Consistent SQLite+private-files directory backup and safe empty-directory restore."""
import argparse, sqlite3, shutil, json, hashlib
from pathlib import Path

def private_directory(path):
    path=Path(path)
    if not path.parent.exists(): private_directory(path.parent)
    path.mkdir(mode=0o700,exist_ok=True)
    path.chmod(0o700)
    return path

def private_copy(source,destination):
    # Copy bytes without inheriting permissive source metadata.
    destination=Path(destination)
    destination.touch(mode=0o600,exist_ok=False)
    destination.chmod(0o600)
    with Path(source).open('rb') as src, destination.open('wb') as dst:
        shutil.copyfileobj(src,dst)

def empty(path):
    path=Path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())): raise ValueError('Destination must be an empty directory or absent')
    return private_directory(path)

def backup(source,destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if source==destination or source in destination.parents: raise ValueError('Backup must be outside source directory')
    if not (source/'episodes.sqlite3').is_file(): raise ValueError('Source database missing')
    destination=empty(destination)
    # BEGIN IMMEDIATE blocks mutations while a separate connection takes a snapshot and files are copied.
    with sqlite3.connect(source/'episodes.sqlite3',timeout=30) as lock:
        lock.execute('BEGIN IMMEDIATE')
        (destination/'episodes.sqlite3').touch(mode=0o600)
        (destination/'episodes.sqlite3').chmod(0o600)
        with sqlite3.connect(source/'episodes.sqlite3') as src, sqlite3.connect(destination/'episodes.sqlite3') as dst: src.backup(dst)
        private_directory(destination/'files')
        for (name,) in lock.execute('SELECT path FROM files'):
            if Path(name).name!=name: raise ValueError('Unsafe file path')
            private_copy(source/'files'/name,destination/'files'/name)
        lock.rollback()
    manifest={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()}
    manifest_path=destination/'backup-manifest.json'
    manifest_path.touch(mode=0o600); manifest_path.chmod(0o600)
    manifest_path.write_text(json.dumps(manifest,indent=2))

def restore(source,destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    manifest=json.loads((source/'backup-manifest.json').read_text())
    for name,sha in manifest.items():
        if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('Unsafe manifest path')
        p=(source/name).resolve()
        if source not in p.parents or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha: raise ValueError('Backup manifest verification failed')
    if 'episodes.sqlite3' not in manifest: raise ValueError('Database missing from backup')
    destination=empty(destination)
    for name in manifest:
        out=destination/name; private_directory(out.parent); private_copy(source/name,out)
    private_directory(destination/'files')

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['backup','restore']); parser.add_argument('source'); parser.add_argument('destination'); args=parser.parse_args()
    try: globals()[args.command](args.source,args.destination)
    except (ValueError,OSError,sqlite3.Error) as exc: parser.exit(1,f'{exc}\n')
