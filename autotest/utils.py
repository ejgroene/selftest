
import multiprocessing
import contextlib
import types            # for creating Function object with extended bindings
import inspect
import sys
import functools        # wrapping function in async generator

is_main_process = multiprocessing.current_process().name == "MainProcess"


def iterate(f, v):
    while v:
        yield v
        v = f(v)


# get the type of Traceback objects
try: raise Exception
except Exception as e:
    TracebackType = type(e.__traceback__)
    del e


def _(): yield
async def _a(): yield
ContextManagerType = type(contextlib.contextmanager(_)())
AsyncContextManagerType = type(contextlib.asynccontextmanager(_a)())
del _, _a


def frame_to_traceback(tb_frame, tb_next=None):
    tb = TracebackType(tb_next, tb_frame, tb_frame.f_lasti, tb_frame.f_lineno)
    return create_traceback(tb_frame.f_back, tb) if tb_frame.f_back else tb


def bind_names(bindings, names, frame):
    """ find names in locals on the stack and binds them """
    # TODO restrict this a bit; now you can poke anywhere
    if not frame or not names:
        return bindings
    if not is_internal(frame):
        f_locals = frame.f_locals # rather expensive
        rest = []
        for name in names:
            try:
                bindings[name] = f_locals[name]
            except KeyError:
                rest.append(name)
    else:
        rest = names
    return bind_names(bindings, rest, frame.f_back)


def bind_1_frame_back(func):
    """ Binds the unbound vars in func to values found on the stack """
    func2 = types.FunctionType(
               func.__code__,                      # code
               bind_names(                         # globals
                   func.__globals__.copy(),
                   func.__code__.co_names,         # names other than arguments and function locals
                   inspect.currentframe().f_back),
               func.__name__,                      # name
               func.__defaults__,                  # default arguments
               func.__closure__)                   # closure/free vars
    func2.__annotations__ = func.__annotations__   # annotations not in __init__
    return func2


def is_builtin(f):
    if m := inspect.getmodule(f.f_code):
        return m.__name__ in sys.builtin_module_names

def is_internal(frame):
    nm = frame.f_code.co_filename
    return 'AUTOTEST_INTERNAL' in frame.f_code.co_varnames or \
           '<frozen importlib' in nm or \
           is_builtin(frame)   # TODO testme


def ensure_async_generator_func(f):
    if inspect.isasyncgenfunction(f):
        return f
    if inspect.isgeneratorfunction(f):
        @functools.wraps(f)
        async def wrap(*a, **k):
            for v in f(*a, **k):
                yield v
        return wrap
    assert False, f"{f} cannot be a async generator."


# using import.import_module in asyncio somehow gives us the frozen tracebacks (which were
# removed in 2012, but yet showing up again in this case. Let's get rid of them.
def asyncio_filtering_exception_handler(loop, context):
    if 'source_traceback' in context:
        context['source_traceback'] = [t for t in context['source_traceback'] if '<frozen ' not in t.filename]
    return loop.default_exception_handler(context)


class ArgsCollector:
    """ collects args before calling a function: ArgsCollector(f, 1)(2)(3)() calls f(1,2,3) """
    def __init__(self, f, *args, **kwds):
        self.func = f
        self.args = args
        self.kwds = kwds
    def __call__(self, *args, **kwds):
        if not args and not kwds:
            return self.func(*self.args, **self.kwds)
        self.args += args
        self.kwds.update(kwds)
        return self


class ArgsCollectingContextManager(ArgsCollector, ContextManagerType):
    """ Context manager that accepts additional args everytime it is called.
        NB: Implementation closely tied to contexlib.py (self.gen)"""
    def __enter__(self):
        self.gen = self()
        return super().__enter__()


class ArgsCollectingAsyncContextManager(ArgsCollector, AsyncContextManagerType):
    async def __aenter__(self):
        self.gen = self()
        return await super().__aenter__()
    @property
    def __enter__(self):
        raise Exception(f"Use 'async with' for {self.func}.")


#@self_test #TODO
def extra_args_supplying_contextmanager():
    def f(a, b, c, *, d, e, f):
        yield a, b, c, d, e, f
    c = ArgsCollectingContextManager(f, 'a', d='d')
    assert isinstance(c, contextlib.AbstractContextManager)
    c('b', e='e')
    with c('c', f='f') as v:
        assert ('a', 'b', 'c', 'd', 'e', 'f') == v, v


