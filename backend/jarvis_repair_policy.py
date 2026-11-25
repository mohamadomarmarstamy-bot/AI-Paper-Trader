"""Pure paper helper repair policy, shared by the server and trusted CI runner.

Generated code cannot import, perform I/O, access credentials, mutate inputs,
change accounting or alter the runner, tests, validation gates or risk constants.
"""
import ast
import hashlib

TARGETS={'jarvis_test_bot.py':{'entry_quote_budget','next_exit'},'jarvis_research.py':{'chart_fresh_for'}}
CALLS={'min','max','any','all','len','sorted','int','float','bool','isinstance','type',
       'exit_queue','stop_distance','retry_group','quote_due_since','chart_is_fresh'}
GLOBALS={'BACKLOG_EXIT_QUOTES_PER_ENTRY','EXIT_QUOTES_PER_ENTRY','NEAR_STOP_POINTS',
         'OLDEST_EXIT_INTERVAL','EXIT_RETRY_INTERVAL','FRESH_SECONDS','local_lab','dict','list','tuple'}
METHODS={'get','items','values','keys'}
NODES=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.Assign,ast.AugAssign,
       ast.If,ast.IfExp,ast.Expr,ast.Constant,ast.Name,ast.Load,ast.Store,ast.Subscript,
       ast.Attribute,ast.Call,ast.keyword,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,
       ast.USub,ast.UAdd,ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,
       ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,
       ast.Is,ast.IsNot,ast.Dict,ast.List,ast.Tuple,ast.Set,ast.ListComp,ast.DictComp,
       ast.GeneratorExp,ast.comprehension,ast.Lambda,ast.Pass)


def check_function(source,file,name,original):
    if file not in TARGETS or name not in TARGETS[file] or not isinstance(source,str) or len(source)>18000:
        raise ValueError('Outside automatic paper repair scope')
    root=ast.parse(source)
    if len(root.body)!=1 or not isinstance(root.body[0],ast.FunctionDef) or root.body[0].name!=name:
        raise ValueError('Expected one helper function')
    fn=root.body[0]
    if ast.dump(fn.args)!=ast.dump(original.args) or fn.decorator_list or fn.returns:
        raise ValueError('Helper signature cannot change')
    nodes=list(ast.walk(root))
    if len(nodes)>1200:raise ValueError('Repair too complex')
    local_names={n.id for n in nodes if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
    local_names|={n.arg for n in nodes if isinstance(n,ast.arg)}
    functions={n.name:n for n in nodes if isinstance(n,ast.FunctionDef)}
    if local_names & (CALLS|GLOBALS|set(functions)):
        raise ValueError('Reserved helper names cannot be rebound')
    if len(functions)!=sum(isinstance(n,ast.FunctionDef) for n in nodes):raise ValueError('Duplicate helper name')
    allowed=CALLS|GLOBALS|local_names|set(functions)
    for node in nodes:
        if not isinstance(node,NODES):raise ValueError('Unsupported helper operation')
        if isinstance(node,ast.FunctionDef) and (node.decorator_list or node.returns or node.type_params):
            raise ValueError('Function metadata is unsupported')
        if isinstance(node,ast.arg) and node.annotation:raise ValueError('Annotations unsupported')
        if isinstance(node,ast.Name) and ((node.id.startswith('_') and node.id!='_') or node.id not in allowed):
            raise ValueError('Unknown helper name')
        if isinstance(node,ast.Attribute):
            if node.attr not in METHODS and not (isinstance(node.value,ast.Name) and node.value.id=='local_lab' and node.attr=='SLOTS'):
                raise ValueError('Attribute access outside paper data')
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name):
                if node.func.id not in CALLS|set(functions):raise ValueError('Unknown helper call')
            elif not isinstance(node.func,ast.Attribute) or node.func.attr not in METHODS:
                raise ValueError('Indirect helper call unsupported')
            if any(k.arg is None for k in node.keywords) or any(isinstance(a,ast.Starred) for a in node.args):raise ValueError('Argument unpacking unsupported')
        if isinstance(node,(ast.Assign,ast.AugAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            if any(not isinstance(t,ast.Name) for t in targets):raise ValueError('Input mutation unsupported')
        if isinstance(node,ast.Constant):
            if type(node.value) not in (str,int,float,bool,type(None)) or (isinstance(node.value,str) and len(node.value)>200):raise ValueError('Invalid helper literal')
            if type(node.value) in (int,float) and not -1_000_000<=node.value<=1_000_000:raise ValueError('Helper literal out of range')
        if isinstance(node,ast.BinOp) and any(isinstance(x,ast.Constant) and isinstance(x.value,str) for x in (node.left,node.right)):
            raise ValueError('String arithmetic unsupported')
        if isinstance(node,ast.comprehension) and node.is_async:raise ValueError('Async unsupported')
    def depth(node,level=0):
        if isinstance(node,(ast.ListComp,ast.DictComp,ast.GeneratorExp)):
            level+=len(node.generators)
            if level>2:raise ValueError('Nested iteration exceeds paper helper bound')
        for child in ast.iter_child_nodes(node):depth(child,level)
    depth(root)
    # Reject helper recursion, including nested functions.
    edges={key:{n.func.id for n in ast.walk(value) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in functions}
           for key,value in functions.items()}
    def visit(key,path):
        if key in path:raise ValueError('Recursive helpers unsupported')
        for child in edges[key]:visit(child,path|{key})
    # Nested definitions are included by ast.walk; only actual calls define cycles.
    for key in functions:visit(key,set())
    return fn


def apply(source,patch):
    if hashlib.sha256(source.encode()).hexdigest()!=patch.get('base_sha256'):raise ValueError('Stale source')
    file,name=patch.get('file'),patch.get('function')
    root=ast.parse(source)
    original=next((n for n in root.body if isinstance(n,ast.FunctionDef) and n.name==name),None)
    if original is None:raise ValueError('Missing helper')
    check_function(patch.get('replacement'),file,name,original)
    lines=source.splitlines(keepends=True)
    candidate=''.join(lines[:original.lineno-1])+patch['replacement'].rstrip()+'\n'+''.join(lines[original.end_lineno:])
    ast.parse(candidate)
    return candidate


def check_test(source,file,name):
    if not isinstance(source,str) or not 1<=len(source)<=12000:raise ValueError('A regression test is required')
    root=ast.parse(source);module=file.removesuffix('.py');aliases={}
    for node in root.body:
        if isinstance(node,ast.Import):
            for alias in node.names:
                if alias.name not in ('unittest',module):raise ValueError('Regression import unsupported')
                aliases[alias.asname or alias.name]=alias.name
        elif not isinstance(node,ast.ClassDef):raise ValueError('Expected unittest class')
    classes=[n for n in root.body if isinstance(n,ast.ClassDef)]
    if not classes or len(classes)>3:raise ValueError('Expected regression tests')
    for cls in classes:
        if cls.decorator_list or cls.keywords or len(cls.bases)!=1 or ast.unparse(cls.bases[0])!='unittest.TestCase':raise ValueError('Expected plain TestCase')
        if any(not isinstance(n,ast.FunctionDef) or not n.name.startswith('test_') for n in cls.body):raise ValueError('Expected test methods only')
    blocked=(ast.ImportFrom,ast.With,ast.Try,ast.Raise,ast.For,ast.While,ast.Global,ast.Nonlocal,ast.Delete,ast.NamedExpr,ast.Lambda)
    assertions={'assertEqual','assertNotEqual','assertTrue','assertFalse','assertGreater','assertGreaterEqual','assertLess','assertLessEqual','assertIn','assertNotIn','assertIsNone','assertIsNotNone'}
    for node in ast.walk(root):
        if isinstance(node,blocked):raise ValueError('Unsupported regression operation')
        if isinstance(node,ast.FunctionDef) and (node.decorator_list or node.returns or len(node.args.args)!=1 or node.args.args[0].arg!='self'):
            raise ValueError('Invalid regression method')
        if isinstance(node,ast.Attribute):
            valid=(isinstance(node.value,ast.Name) and ((node.value.id=='self' and node.attr in assertions)
                or (aliases.get(node.value.id)==module and node.attr==name)
                or (node.value.id=='unittest' and node.attr=='TestCase')))
            if not valid:raise ValueError('Regression introspection unsupported')
        if isinstance(node,ast.Call) and not isinstance(node.func,ast.Attribute):raise ValueError('Unknown regression call')
        if isinstance(node,ast.Assign) and any(not isinstance(n,ast.Name) for n in node.targets):raise ValueError('Regression mutation unsupported')
        if isinstance(node,ast.Name) and node.id.startswith('_'):raise ValueError('Private regression name')
    return True
