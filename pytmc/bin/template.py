"""
"pytmc template" is a command line utility to expand a template based on a
TwinCAT3 project (or XML-format file such as .TMC).
"""

import argparse
import functools
import logging
import pathlib
import sys

import jinja2

from .. import parser, pragmas
from . import stcmd, summary, util

DESCRIPTION = __doc__
logger = logging.getLogger(__name__)


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        'projects', type=str,
        help='Path to project or solution (.tsproj, .sln)',
        nargs='+',
    )

    argparser.add_argument(
        '-t', '--template',
        dest='templates',
        type=argparse.FileType(mode='r'),
        help='Template filename (default: standard input)',
        action='append',
    )

    argparser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Post template generation, open an interactive Python session'
    )

    return argparser


def project_to_dict(path):
    """
    """
    path = pathlib.Path(path)
    suffix = path.suffix.lower()

    if suffix in {'.tsproj', '.sln'}:
        if suffix == '.sln':
            project_files = parser.projects_from_solution(path)
            solution = path
        else:
            project_files = [path]
            solution = None

        projects = {
            fn: parser.parse(fn)
            for fn in project_files
        }

        solutions = {solution: projects} if solution is not None else {}

        return {
            'solutions': solutions,
            'projects': projects,
        }

    return {
        'others': {path: parser.parse(path)},
    }


def projects_to_dict(*paths) -> dict:
    """
    """
    result = {
        'solutions': {},
        'projects': {},
        'others': {},
    }
    for path in paths:
        for key, value in project_to_dict(path).items():
            result[key].update(value)
    return result


def get_jinja_filters(**user_config):
    """All jinja filters."""

    if 'delim' not in user_config:
        user_config['delim'] = ':'

    if 'prefix' not in user_config:
        user_config['prefix'] = 'PREFIX'

    @jinja2.evalcontextfilter
    def epics_prefix(eval_ctx, obj):
        return stcmd.get_name(obj, user_config)[0]

    @jinja2.evalcontextfilter
    def epics_suffix(eval_ctx, obj):
        return stcmd.get_name(obj, user_config)[1]

    @jinja2.evalcontextfilter
    def pragma(eval_ctx, obj, key, default=''):
        item_and_config = pragmas.expand_configurations_from_chain([obj])
        if item_and_config:
            item_to_config = dict(item_and_config[0])
            config = pragmas.squash_configs(*item_to_config.values())
            return config.get(key, default)
        return default

    return {
        key: value for key, value in locals().items()
        if not key.startswith('_')
    }


@functools.lru_cache()
def get_symbols(plc) -> dict:
    """Get symbols for the PLC."""
    return parser.separate_by_classname(plc.find(parser.Symbol))


@functools.lru_cache()
def get_motors(plc) -> list:
    """Get motor symbols for the PLC (DUT_MotionStage)."""
    symbols = get_symbols(plc)
    return [
        mot for mot in symbols['Symbol_DUT_MotionStage']
        if not mot.is_pointer
    ]


@functools.lru_cache()
def get_nc(project) -> parser.NC:
    """Get the top-level NC settings for the project."""
    try:
        nc, = list(project.find(parser.NC, recurse=False))
        return nc
    except Exception:
        return None


@functools.lru_cache()
def get_data_types(project):
    """Get the data types container for the project."""
    data_types = getattr(project, 'DataTypes', [None])[0]
    if data_types is not None:
        return summary.enumerate_types(data_types)


@functools.lru_cache()
def get_boxes(project) -> list:
    """Get boxes contained in the project."""
    return list(
        sorted(project.find(parser.Box),
               key=lambda box: int(box.attributes['Id']))
    )


@functools.lru_cache()
def get_links(project) -> list:
    """Get links contained in the project or PLC."""
    return list(project.find(parser.Link))


def render_template(template: str, context: dict,
                    trim_blocks=True, lstrip_blocks=True,
                    **env_kwargs):
    """
    One-time-use jinja environment + template rendering helper.
    """
    env = jinja2.Environment(
        loader=jinja2.DictLoader({'template': template}),
        trim_blocks=trim_blocks,
        lstrip_blocks=lstrip_blocks,
        **env_kwargs,
    )

    env.filters.update(get_jinja_filters())
    return env.get_template('template').render(context)


helpers = [
    get_symbols,
    get_motors,
    get_nc,
    get_data_types,
    get_boxes,
    get_links,
    parser.get_data_type_by_reference,
    parser.get_pou_call_blocks,
    parser.separate_by_classname,
    parser.element_to_class_name,
    parser.determine_block_type,
    summary.data_type_to_record_info,
    summary.enumerate_types,
    summary.list_types,
    min,
    max,
]


def get_render_context() -> dict:
    """Jinja template context dictionary - helper functions."""
    context = {func.__name__: func for func in helpers}
    context['types'] = parser.TWINCAT_TYPES
    return context


def main(projects, templates=None, debug=False):
    """
    """

    if templates is None:
        logger.warning('Reading template from standard input...')
        templates = [sys.stdin.read()]
        logger.warning('Read template from standard input (len=%d)',
                       len(templates[0]))
    else:
        templates = [template.read() for template in templates]

    template_args = get_render_context()
    template_args.update(projects_to_dict(*projects))

    stashed_exception = None
    rendered = []
    try:
        for template in templates:
            rendered.append(render_template(template, template_args))
    except Exception as ex:
        stashed_exception = ex
        rendered = None

    if not debug:
        if stashed_exception is not None:
            raise stashed_exception
        print('\n'.join(rendered))
    else:
        message = ['Variables: projects, templates, template_args. ']
        if stashed_exception is not None:
            message.append(f'Exception: {type(stashed_exception)} '
                           f'{stashed_exception}')

        util.python_debug_session(
            namespace=locals(),
            message='\n'.join(message)
        )
    return projects
