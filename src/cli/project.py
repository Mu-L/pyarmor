#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
#############################################################
#                                                           #
#      Copyright @ 2024 Dashingsoft corp.                   #
#      All rights reserved.                                 #
#                                                           #
#      pyarmor                                              #
#                                                           #
#      Version: 9.1.0 -                                     #
#                                                           #
#############################################################
#
#
#  @File: pyarmor/cli/project.py
#
#  @Author: Jondy Zhao(pyarmor@163.com)
#
#  @Create Date: Tue Nov 12 16:38:51 CST 2024
#
#  @Description:
#
#   - Define project object for Pyarmor 9.
#   - Define project commands: init, build
#   - Define 3 targets: std, mini, plain

"""Manage projects

Config View
-----------

[project]
global_excludes = .* __pycache__
project_includes = *.py *.pyw
package_includes = *.py *.pyw
recursive = 0

name = str
src = absolute path

modules = pattern pattern ...
packages = pattern path@name @section ...
excludes = patterh patterh ...

rpath = path path

[project.name]
name =
path =

modules =
packages = 0 or 1
excludes =

rpath =

Examples
--------

1. Create a project in current path

    $ pyarmor init

2. Obfuscate all the scripts in the project

    $ pyarmor build --rft
    $ pyarmor build --mini

3. Config project and print project information

    $ pyarmor env -p
    $ pyarmor env -p set rft_remove_docstr 1

"""
import ast
import glob
import logging
import os
import tokenize

from fnmatch import fnmatch
from collections import namedtuple
from os.path import (
    abspath, basename, exists, isabs, join as joinpath,
    normpath, relpath, splitext
)
from string import Template
from textwrap import dedent


logger = logging.getLogger('cli.project')


GRAPHVIZ_INDENT = '  '

############################################################
#
# Project File View
#
############################################################

GLOBAL_EXCLS = '.*', '__pycache__'
GLOBAL_INCLS = '*.py', '*.pyw'

ProjectItem = namedtuple(
    'ProjectItem',
    ('name', 'src', 'scripts', 'modules', 'packages',
     'excludes', 'recursive'),
    defaults=['', '', [], [], [], None, False]
)


def scan_path(path, includes=None, excludes=[], **options):
    files, dirs = [], []
    xlist = includes if includes else GLOBAL_INCLS
    with os.scandir(path) as itdir:
        for et in itdir:
            if any([fnmatch(et.name, x) for x in excludes]):
                continue
            if et.is_dir(follow_symlinks=False):
                dirs.append(et.name)
            elif (et.is_file(follow_symlinks=False) and
                  any([fnmatch(et.name, x) for x in xlist])):
                files.append(et.name)
    return files, dirs


def search_item(root, pattern, excludes, recursive=0):
    if not pattern:
        return []

    sep = os.sep if pattern.endswith(os.sep) else ''
    result = []

    pt = pattern if isabs(pattern) else joinpath(root, pattern)
    for item in glob.glob(pt, recursive=recursive):
        name = basename(item.strip(sep))
        if excludes and any([fnmatch(name, x) for x in excludes]):
            continue
        result.append(item)
    return [normpath(x) for x in result]


############################################################
#
# Concepts
#
############################################################

class Module:
    """Module concept"""

    def __init__(self, path, name=None, parent=None):
        self.parent = parent
        self._path = path
        self._name = name if name else splitext(basename(path))[0]

        self._co = None
        self._tree = None
        self._type = None

    @property
    def name(self):
        return '' if self._name == '__init__' else self._name

    @property
    def path(self):
        return self._path

    @property
    def mtype(self):
        return self._type

    @property
    def mtree(self):
        return self._tree

    @property
    def qualname(self):
        if isinstance(self.parent, (Project, type(None))):
            return self._name
        prefix = self.parent.qualname + ('.' if self.name else '')
        return prefix + self.name

    @property
    def project(self):
        """Return project this module belong to"""
        return (self.parent if isinstance(self.parent, Project)
                else self.parent.project)

    @property
    def abspath(self):
        return (self._path if isabs(self._path) else
                joinpath(self.parent.abspath, self._path))

    def compile_file(self, force=False):
        if self._co is not None and not force:
            return

        self.parse_file(force=force)

        logger.info('compile %s ...', self.qualname)
        self._co = compile(self._tree, self.abspath, 'exec')
        logger.info('compile %s end', self.qualname)

    def parse_file(self, force=False):
        if self._tree is not None and not force:
            return

        filename = self.abspath
        with open(filename, 'rb') as f:
            encoding, _ = tokenize.detect_encoding(f.readline)

        with open(filename, 'r', encoding=encoding) as f:
            logger.info('parse %s ...', self.qualname)
            self._tree = ast.parse(f.read(), filename, 'exec')
            logger.info('parse %s end', self.qualname)

    def _as_dot(self):
        return self.name


class Script(Module):
    """Script concept"""

    @property
    def rpaths(self):
        """Extra Python paths for importing module

        If one script has any extra pypath, it need map imported
        module name to project module name

        For example, in the script `import abc`, maybe it uses module
        `pkg.abc` in this project

        This is only used by RFT mode, otherwise it doesn't know where
        to find module `abc`

        This property is used to generate internal `_mapped_modules`
        """
        pass


class Package(Module):
    """Package concept"""

    def __init__(self, path, name=None, parent=None):
        super().__init__(path, name=name, parent=parent)

        self._modules = None
        self._packages = None

    def load(self):
        excludes = GLOBAL_EXCLS
        files, dirs = scan_path(self.abspath, excludes=excludes)
        self._modules = [Module(x, parent=self) for x in files]
        self._packages = [Package(x, parent=self) for x in dirs]

    @property
    def modules(self):
        """Each package has many modules

        There is one special module `__init__` for Package
        """
        if self._modules is None:
            self.load()

        for x in self._modules:
            yield x

    @property
    def packages(self):
        """Each package has many sub-packages"""
        if self._packages is None:
            self.load()

        for x in self._packages:
            yield x

    def iter_module(self):
        if self._modules is None or self._packages is None:
            self.load()

        for x in self._modules:
            yield x

        for pkg in self._packages:
            for x in pkg.iter_module():
                yield x

    def _as_dot(self, n=0):
        modules = [x._as_dot() for x in self.modules]
        packages = [x._as_dot(n+1) for x in self.packages]
        sep = '\n' + GRAPHVIZ_INDENT
        source = Template(dedent("""\
        subgraph cluster_$cid {
          label="$name";
          $modules
          $packages
        }""")).substitute(
            cid=id(self),
            name=self.name,
            modules=sep.join(modules),
            packages=sep.join(packages),
        )
        return (
            ('\n' + GRAPHVIZ_INDENT * n).join(source.splitlines())
            if n else source
        )


class Namespace:
    """Namespace concept"""

    @property
    def name(self):
        pass

    @property
    def components(self):
        """Each component has 3 items: path, modules, children

        Each child is Namespace or Package
        """
        return []


class Project:
    """Project conpect

    Project is compose of Python elements and obfuscation settings

    Each project has 4 components:

      - Script
      - Module
      - Package
      - Namespace

    Each component has one unique name in this project except Script

    It may has alias which also can't be duplicated with other names

    Refer
    -----

    https://docs.python.org/3.13/reference/import.html#namespace-packages

    """

    def __init__(self, ctx):
        self.ctx = ctx
        self.src = ''

        self._scripts = []
        self._modules = []
        self._packages = []
        self._namespaces = []

        self._rft_options = None
        self._rft_filters = None
        self._rft_rulers = None

        self._rmodules = None
        self._builtins = None

        # Log variable name in chain attributes
        self.unknown_vars = []

        # Log attribute used but not defined in class
        self.unknown_attrs = {}

        # Log function which called with **kwarg
        self.unknown_calls = []

        # Log unknown caller with keyword arguments
        self.unknown_args = []

    @property
    def abspath(self):
        return abspath(self.src)

    @property
    def scripts(self):
        """Project entry points

        One project may has many entry points

        Script can't be imported by other components
        """
        for x in self._scripts:
            yield x

    @property
    def modules(self):
        for x in self._modules:
            yield x

    @property
    def packages(self):
        """Only top packages"""
        for x in self._packages:
            yield x

    @property
    def namespaces(self):
        """Only top namespace"""
        for x in self._namespaces:
            yield x

    @property
    def rft_options(self):
        """Refactor options:

        - rft_remove_assert: bool

          If 1, remove assert node

        - rft_remove_docstr: bool

          If 1, remove all docstring

        - rft_builtin: bool

          0, do not touch any builtin names
          1, as build target, maybe std, mini or plain

        - rft_import: bool

          always 1

        - rft_ximport: bool

          always 0, do not touch node "from..import *"

        - rft_argument: enum('no', 'pos', '!kw', 'all')

          0: "no", no reform any argument node
          1: "pos", reform posonly arguments
          2: '!kw', no reform keyword only arguments
          3: "all", reform all arguments

          Note that if function is exported, no arguments reformed

        - obf_attribute: enum(no, yes, all)

          Reform attribute node to setattr() or getattr()

          0, do not reform attribute to setattr or getattr
          1, reform attribute node by rft_attribute_filters
          2, reform all attribute node

        - obf_string: enum(no, yes, all)

          Reform string constant to security mode

          It only works for std/mini target

          It's always 0 for plain target

          0, no reform string
          1, reform string by rft_string_filters
          2, reform all string

        - rft_auto_export: bool

          Auto export all names in module.__all__

        - rft_exclude_names: list

          Move it from rft_filter

        - rft_exclude_args: list

          Move it from rft_filter

        - rft_str_keywords: list (not implemented)

          Rename string constant or key in dict constant

          When call function, solve argument not found issue

        - var_type_table: dict

          Specify variable type

        - extra_type_info: dict

          Specify extra attribute for module or type

        - wildcard_import_table: dict (unused now)

          When building target, it need import module to get name
          for wildcard import, sometimes it may failed

          In order to avoid importing module in build time, users
          can provide all names in wildcard imported module

        - extra_builtins: list

          By default, builtin names is got from builtin module

          User can append extra builtin names

        - on_unknown_attr: enum(ask, log, yes, no, err)

          When don't know how to refactor attribute

            ask: query user interactively
            log: no touch attr but log it
            yes: rename attr
            no: do thing
            err: raise error
        """
        if self._rft_options is None:
            cfg = self.ctx.cfg
            sect = 'rft_option'
            if cfg.has_section(sect):
                self._rft_options = dict(cfg.items(sect))
            else:
                self._rft_options = {}
        return self._rft_options

    def opt(self, name):
        return self.rft_options.get(name)

    @property
    def rft_exclude_names(self):
        """Exclude module, class, function

        All names in this scope aren't renamed. For example,

        Exclude module, all classes and functions aren't renamed
        Exclude class, all class attributes aren't renamed

        Each ruler is one chained names
        Each ruler must start with package or module name
        It supports pattern match as fnmatchcase
        Pattern only match one level

        It's also used to export names manually
        """
        value = self.opt('rft_exclude_names')
        if value:
            for x in value.splitlines():
                yield x

    @property
    def rft_exclude_args(self):
        """No refactor arguments of the functions in this list"""
        value = self.opt('rft_exclude_args')
        if value:
            for x in value.splitlines():
                yield x

    @property
    def rft_filters(self):
        if self._rft_filters is None:
            cfg = self.ctx.cfg
            sect = 'rft_filter'
            if cfg.has_section(sect):
                self._rft_filters = dict(cfg.items(sect))
            else:
                self._rft_filters = {}
        return self._rft_filters

    @property
    def obf_include_strings(self):
        """A list of re pattern based on obf_string

        All matched string in ast.Tree will be transformed
        """
        value = self.rft_filters.get('obf_include_strings', '')
        for x in value.splitlines():
            yield x

    @property
    def obf_attr_filters(self):
        """A list of re pattern based on obf_attribute

        All matched ast.Attribute will be transformed to call
        setattr() or getattr() to hide attribute name
        """
        value = self.rft_filters.get('obf_attr_filters', '')
        for x in value.splitlines():
            yield x

    @property
    def rft_rulers(self):
        if self._rft_rulers is None:
            cfg = self.ctx.cfg
            sect = 'rft_ruler'
            if cfg.has_section(sect):
                self._rft_rulers = dict(cfg.items(sect))
            else:
                self._rft_rulers = {}
        return self._rft_rulers

    @property
    def rft_attr_rulers(self):
        """Refactor attribute rulers, for special attribute node

          If can't decide variable type, use ruler for chains

          For example, "x.a", if "x" of type is unknown

          Use ruler "x.a" to rename attribute "a"

          Use ruler "!x.a" to keep attribute "a"
        """
        value = self.rft_rulers.get('rft_attr_rulers', '')
        for x in value.splitlines():
            yield x

    @property
    def rft_arg_rulers(self):
        """Refactor ruler, for arg name in Function/Call node

          For example, in the call statement

            kwargs = { 'msg': 'hello' }
            foo(**kwargs)

          This kind of rule could be used to rename string `msg`
        """
        value = self.rft_rulers.get('rft_arg_rulers', '')
        for x in value.splitlines():
            yield x

    @property
    def builtins(self):
        if self._builtins is None:
            import builtins
            self._builtins = set(dir(builtins))
        return self._builtins

    def std_options(self):
        """Obfuscation options only for std target

        - std_assert_import
        - std_assert_call
        - std_restrict_module
        - std_expired_date
        - std_bind_devices

        Got from command line, not in config file
        """
        pass

    def get_module(self, qualname):
        """Get module in the project by unique qualname
        It equals one dict: map_qualname_to_module
        """
        if self._rmodules is None:
            self._rmodules = {
                x.qualname: x for x in self.iter_module()
            }
        return self._rmodules.get(qualname)

    def iter_module(self):
        """Iterate all modules in this project"""
        for x in self._modules:
            yield x

        for child in self._packages + self._namespaces:
            for x in child.iter_module():
                yield x

    def relsrc(self, path):
        return relpath(path, self.src)

    def load(self, data):
        """Init project object with dict

        It equals:

        1. map init data to ProjectItem
        2. map ProjectItem to project files
        """
        dp = joinpath(self.ctx.local_path, 'project')
        os.makedirs(dp, exist_ok=True)

        def vlist(name):
            return [x.strip().replace('%20%', ' ')
                    for x in data.get(name, '').split()]

        src = self.src = data['src']
        name = data.get('name')
        excludes = vlist('excludes') + list(GLOBAL_EXCLS)

        scripts = []
        for pat in vlist('scripts'):
            scripts.extend(search_item(src, pat, excludes))
        self._scripts.extend([
            Script(self.relsrc(x), parent=self) for x in scripts
        ])

        modules = []
        for pat in vlist('modules'):
            modules.extend(search_item(src, pat, excludes))

        packages = vlist('packages')
        if packages:
            for item in packages:
                # 3 forms: path, path@name, @sect
                i = item.find('@')
                if i == 0:
                    logger.info('package at section %s', item)
                    continue
                if i == -1:
                    path, pkgname = item, basename(item)
                else:
                    path, pkgname = item.split('@')
                if not isabs(path):
                    path = joinpath(src, path)
                obj = Package(path, name=pkgname, parent=self)
                self._packages.append(obj)

        elif not modules and not scripts:
            pkginit = joinpath(src, '__init__.py')
            if exists(pkginit):
                pkgname = name if name else basename(src)
                obj = Package(src, name=pkgname, parent=self)
                self._packages.append(obj)
            else:
                files, dirs = scan_path(src, excludes=excludes)
                modules.extend([joinpath(src, x) for x in files])
                self._packages.extend([
                    Package(x, parent=self) for x in dirs
                ])

        if scripts and modules:
            for x in set(scripts) & set(modules):
                logger.debug('duplicated %s', self.relsrc(x))
                modules.remove(x)
        self._modules.extend([
            Module(self.relsrc(x), parent=self) for x in modules
        ])

        logger.info('load %d scripts', len(self._scripts))
        logger.info('load %d modules', len(self._modules))
        logger.info('load %d packages', len(self._packages))

    def log_unknown_var(self, module, var):
        key = '%s:%s' % (module, var)
        if key not in self.unknown_vars:
            self.unknown_vars.append(key)

    def log_unknown_call(self, func):
        if isinstance(func, str):
            fields = func.split(':')
            if fields[2].find('.') == -1:
                self.log_unknown_var('%s:%s.%s' % fields)
            elif func not in self.unknown_args:
                self.unknown_args.append(func)
        else:
            name = ':'.join([func.module, '.'.join(func.scopes)])
            if name not in self.unknown_calls:
                self.unknown_calls.append(name)

    def _as_dot(self):
        """Map project to dot graph"""
        modules = [x._as_dot() for x in self.modules]
        packages = [x._as_dot() for x in self.packages]
        sep = '\n' + GRAPHVIZ_INDENT * 2
        return Template(dedent("""\
        graph {
          layout=osage
          subgraph cluster_0 {
            label="Project Structure";
            $modules
            $packages
          }
        }""")).substitute(
            modules=sep.join(modules),
            packages=sep.join('\n'.join(packages).splitlines())
        )


if __name__ == '__main__':
    pass