"""Statically named accesses to imported Python namespaces."""


def passive_decorators(root, files, path, walk):
    """Recognize standard wrappers whose application never invokes the target."""
    allowed = {'staticmethod', 'classmethod'}
    if any(n.type == 'import_statement' and n.text == b'import unittest' for n in root.named_children):
        prefix = path.rpartition('/')[0]
        if not any(p in files for folder in {'', prefix} for leaf in ('unittest.py', 'unittest/__init__.py')
                   for p in [(folder+'/' if folder else '')+leaf]):
            allowed.add('unittest')
    # A binding or non-attribute use invalidates this optimization module-wide.
    for n in walk(root):
        if n.type != 'identifier' or n.text.decode() not in allowed:
            continue
        owner = n.parent
        if owner.type == 'decorator':
            continue
        if n.text == b'unittest' and (owner.type == 'dotted_name' and (owner.parent.text == b'import unittest'
                                      or owner.parent.type == 'import_from_statement' and owner.parent.child_by_field_name('module_name') == owner)
                                      or owner.type == 'attribute' and owner.child_by_field_name('object') == n
                                      and owner.parent.type not in ('assignment', 'augmented_assignment', 'delete_statement')):
            continue
        allowed.discard(n.text.decode())
    return allowed


def passive_definition(node, allowed):
    if node.type != 'decorated_definition':
        return True
    for wrapper in (n for n in node.named_children if n.type == 'decorator'):
        value = wrapper.named_children[0]
        if value.type == 'identifier' and value.text.decode() in allowed & {'staticmethod', 'classmethod'}:
            continue
        function = value.child_by_field_name('function')
        if ('unittest' in allowed and value.type == 'call' and function is not None
                and function.text in (b'unittest.skipIf', b'unittest.skipUnless')):
            continue
        return False
    return True


def namespace_accesses(members, alias):
    """Return referenced bindings and possible members if a binding is a class."""
    selected = {}
    for node in members:
        if node.type != 'identifier' or node.text.decode() != alias:
            continue
        parent = node.parent
        if parent.type == 'attribute' and parent.child_by_field_name('attribute') == node:
            continue
        if parent.type != 'attribute' or parent.child_by_field_name('object') != node:
            return None
        attr = parent.child_by_field_name('attribute').text.decode()
        if attr.startswith('__'):
            return None
        methods = selected.setdefault(attr, set())
        use = parent.parent
        if use.type == 'attribute' and use.child_by_field_name('object') == parent:
            methods.add(use.child_by_field_name('attribute').text.decode())
            continue
        if use.type == 'call' and use.child_by_field_name('function') == parent:
            assignment = use.parent
            target = assignment.child_by_field_name('left')
            if assignment.type == 'assignment' and target.type == 'identifier' and assignment.child_by_field_name('right') == use:
                for ref in members:
                    if ref.type != 'identifier' or ref.text != target.text or ref == target:
                        continue
                    access = ref.parent
                    if access.type == 'attribute' and access.child_by_field_name('object') == ref:
                        methods.add(access.child_by_field_name('attribute').text.decode())
                    else:
                        methods.add('*')
                continue
        methods.add('*')
    return selected


def namespace_alias(node):
    if node.type != 'import_statement':
        return None
    names = node.children_by_field_name('name')
    if len(names) != 1:
        return None
    original = names[0].child_by_field_name('name') or names[0]
    if b'.' in original.text:
        return None
    return (names[0].child_by_field_name('alias') or original).text.decode()
