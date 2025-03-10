#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import json

from typing import Dict

from lookyloo.helpers import get_homedir

if __name__ == '__main__':
    dest_dir = get_homedir() / 'website' / 'web'

    to_save: Dict = {'static': {}}

    for resource in (dest_dir / 'static').glob('*'):
        with resource.open('rb') as f:
            to_save['static'][resource.name] = base64.b64encode(hashlib.sha512(f.read()).digest()).decode('utf-8')

    with (dest_dir / 'sri.txt').open('w') as fw:
        json.dump(to_save, fw, indent=2, sort_keys=True)
