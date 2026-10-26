#!/usr/bin/env python3
"""Add engine sound to a target backup without replacing its riding logic or imports."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

REPO = Path(__file__).resolve().parents[2]
MUSIC_STATE = {'music-play-generation', 'music-active-tracks', 'music-starting', 'music-upload-last'}
MUSIC_FUNCTIONS = ('advance-music-generation', 'music-generation-current', 'music-track-finished',
                   'play-midi-track', 'stop-music', 'play-current-song', 'play-axelf', 'note-on', 'note-off')
PROTECTED_FUNCTIONS = ('check-shutdown', 'send-status2', 'send_stats', 'kers-fade-target',
                       'update-kers-fade', 'kers-voltage-scale', 'update-kers-current')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


warp = load(REPO / 'tools/warp-sound/build_private.py', 'engine_warp_builder')
flash = load(REPO / 'tunu-flash.py', 'engine_flash_builder')


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('expected one source hook: ' + before)
    return source.replace(before, after)


def add_engine(source, canonical=None):
    canonical = canonical if canonical is not None else (REPO / 'app.lisp').read_text()
    if re.search(r'^\(defun engine-step\b', source, re.M):
        raise ValueError('source already contains engine sound; review its upgrade separately')
    protected = {name: warp.extract_defun(source, name) for name in PROTECTED_FUNCTIONS}
    before_imports = flash.source_imports(source.encode())
    states = []
    for line in canonical.splitlines():
        match = re.fullmatch(r'\(def ([A-Za-z0-9_-]+) .+\)', line)
        if match and (match[1].startswith('engine-') or match[1] in MUSIC_STATE):
            existing = re.findall(r'^\(def ' + re.escape(match[1]) + r'\s.+\)$', source, re.M)
            if len(existing) > 1:
                raise ValueError('duplicate state: ' + match[1])
            if not existing:
                states.append(line)
    if not states:
        raise ValueError('canonical engine state is missing')
    source = replace_once(source, '\n(def music-t-start (systime))\n',
                          '\n(def music-t-start (systime))\n' + '\n'.join(states) + '\n')
    functions = re.findall(r'^\(defun ((?:engine-[A-Za-z0-9_-]+)|handle-engine-command|send-engine-status(?:-locked)?)\s', canonical, re.M)
    if 'engine-step' not in functions or 'engine-loop' not in functions:
        raise ValueError('canonical engine functions are missing')
    additions = []
    for name in (*functions, *MUSIC_FUNCTIONS):
        definition = warp.extract_defun(canonical, name)
        if re.search(r'^\(defun ' + re.escape(name) + r'\s', source, re.M):
            source = warp.replace_defun(source, name, definition)
        else:
            additions.append(definition)
    source = replace_once(source, '; ===== Music: polyphony engine =====',
                          '\n\n'.join(additions) + '\n\n; ===== Music: polyphony engine =====')
    for name in ('handle-upload-begin', 'handle-upload-chunk', 'handle-upload-end'):
        definition = warp.extract_defun(source, name)
        opening = '(defun ' + name + ' (data) {'
        update = opening + '\n    (def music-upload-last (systime))'
        if name == 'handle-upload-begin':
            update += '\n    (engine-pause)'
        source = warp.replace_defun(source, name, replace_once(definition, opening, update))
    source = replace_once(source, '    ; Music_Control_ID2 (0x4E9) - master volume',
                          '    (if (= id 0x4EAu32) (handle-engine-command data))\n\n'
                          '    ; Music_Control_ID2 (0x4E9) - master volume')
    source = replace_once(source, '(send-music-status)\n    })\n\n    ; Control_Message_ID1',
                          '(send-music-status)\n        (send-engine-status)\n    })\n\n    ; Control_Message_ID1')
    source = replace_once(source, '    (def dataArray_0x7EC (array-create 1))\n',
                          '    (def dataArray_0x7EC (array-create 1))\n    (def dataArray_0x7ED (array-create 4))\n')
    source = replace_once(source, '    (ext-midi-init 2) ; 2 simultaneous MIDI parsers = max 2 tracks per song\n',
                          '    (ext-midi-init 2) ; 2 simultaneous MIDI parsers = max 2 tracks per song\n    (engine-init)\n')
    source = replace_once(source, '    (spawn 200 decay-loop)\n',
                          '    (spawn 200 decay-loop)\n    (spawn 300 engine-loop)\n')
    source = replace_once(source, '(list abs loopforeach looprange loopwhile)',
                          '(list abs sin loopforeach looprange loopwhile)')
    source, versions = re.subn(r'^\(def sw-app-version [0-9]+\)$', '(def sw-app-version 3)', source, flags=re.M)
    if versions != 1:
        raise ValueError('expected one app version')
    if '(def warp-samples-enabled true)' in source:
        for name in ('warp-finish-if-current', 'play-warp-samples'):
            source = warp.replace_defun(source, name, warp.extract_defun(warp.WARP_FUNCTIONS, name))
    for name, definition in protected.items():
        if warp.extract_defun(source, name) != definition:
            raise ValueError('protected target function changed: ' + name)
    if flash.source_imports(source.encode()) != before_imports:
        raise ValueError('target imports changed')
    return source


def build(backup, output):
    backup = warp.require_private_path(backup, 'target backup')
    output = warp.prepare_private_output(output)
    original = (backup / 'before.lisp').read_text()
    source = add_engine(original)
    metadata = json.loads((backup / 'installed-imports.json').read_text())
    wanted = {tag for _, tag in flash.source_imports(source.encode())}
    overrides = {}
    for item in metadata:
        tag = item['tag']
        if tag not in wanted or tag in overrides:
            raise ValueError('unexpected or duplicate backup import: ' + tag)
        path = backup / ('installed-' + tag + '.bin')
        data = path.read_bytes()
        if len(data) + 1 != item['length'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('backup import mismatch: ' + tag)
        overrides[tag] = path
    if wanted != overrides.keys():
        raise ValueError('missing target backup imports')
    image, _ = flash.build_image(source.encode(), overrides=overrides)
    packed, imports = flash.unpack_blob(flash.validate_image(image))
    if packed != source.encode() or any(imports[tag] != path.read_bytes() + b'\0' for tag, path in overrides.items()):
        raise ValueError('image readback differs from target resources')
    for path, tag in flash.source_imports(source.encode()):
        if not path.startswith(('pkg', '://')):
            destination = (output / path).resolve()
            if output not in destination.parents or destination.name in ('app.lisp', 'engine.img', 'build.json'):
                raise ValueError('import path escapes/collides with output: ' + path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(overrides[tag], destination)
    (output / 'app.lisp').write_text(source)
    (output / 'engine.img').write_bytes(image)
    manifest = {'image_bytes': len(image), 'image_sha256': hashlib.sha256(image).hexdigest(),
                'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                'before_source_sha256': hashlib.sha256(original.encode()).hexdigest(),
                'imports': metadata, 'app_version': 3, 'protected_functions': list(PROTECTED_FUNCTIONS)}
    (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('engine image: %d/%d bytes; %d target imports preserved' % (len(image), flash.MAX_IMAGE_SIZE, len(imports)))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    build(args.backup_dir, args.output_dir)


if __name__ == '__main__':
    main()
