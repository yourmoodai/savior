"""Declaration and deferred-function boundaries in JavaScript/TypeScript syntax."""


def declaration(node):
    if node.type == 'export_statement':
        return next((n for n in node.named_children if n.type in (
            'class_declaration', 'function_declaration', 'generator_function_declaration',
            'lexical_declaration')), node)
    return node


def import_bindings(node):
    result = set()
    pending = list(node.named_children)
    while pending:
        child = pending.pop()
        if child.type == 'import_specifier':
            name = child.child_by_field_name('alias') or child.child_by_field_name('name')
            result.add(name.text.decode())
        elif child.type == 'namespace_import':
            result.update(n.text.decode() for n in child.named_children if n.type == 'identifier')
        elif child.type == 'import_clause':
            result.update(n.text.decode() for n in child.named_children if n.type == 'identifier')
            pending.extend(child.named_children)
        elif child.type not in ('string', 'identifier'):
            pending.extend(child.named_children)
    return result


def deferred_body(node):
    target = declaration(node)
    if target.type in ('function_declaration', 'generator_function_declaration', 'method_definition'):
        return target.child_by_field_name('body')
    if target.type == 'lexical_declaration':
        variables = [n for n in target.named_children if n.type == 'variable_declarator']
        if len(variables) == 1:
            value = variables[0].child_by_field_name('value')
            if value is not None and value.type in ('arrow_function', 'function_expression'):
                return value.child_by_field_name('body')
    return None
