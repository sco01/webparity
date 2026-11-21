# SPDX-License-Identifier: BSD-2-Clause
"""The issue workflow: draft → review → a person files it → register → watch → verify. See docs/issues.md.

webparity never files issues, posts comments or opens pull requests: a person reviews and files everything.

Entry points (import them from their modules; the module names are also the function names):
``drafts.create_draft``, ``drafts.check_draft``, ``register.register``, ``register.register_comment``,
``github.watch``, ``verify.verify`` (and ``verify.format_report``), ``index.generate_index``,
``import_gallery.import_gallery``.
"""
