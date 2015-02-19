# -*- coding: utf-8 -*-
"""Fabric deploy for Drupal 8"""

import os

from fabric.api import env, lcd, local, put, run, runs_once, settings, sudo, \
    task
from fabric.colors import red
from fabric.utils import abort, warn
import yaml


@task
@runs_once
def site(site_name, tier):
    """Load configuration from YAML file
    :param site_name: Site name
    :type site_name: str
    :param tier: Staging tier
    :type tier: str
    """
    env.site_name = site_name
    env.tier = tier

    site_config_filename = 'config/{site_name}.yml'.format(**env)

    try:
        site_config_file = open(site_config_filename, 'r')
    except IOError:
        abort('Unable to read {site_config_filename}'.format(
            site_config_filename=site_config_filename))
    else:
        site_config_data = yaml.safe_load(site_config_file)
        site_config_file.close()

        global_config_data = site_config_data['_global']
        env.update(global_config_data)

        tier_config_data = site_config_data[tier]
        env.update(tier_config_data)

        env.site_build_dir = os.path.join(env.build_dir, env.site_name)
        env.site_repo_dir = os.path.join(env.site_build_dir, 'repo')
        env.tar_basename = '{site_name}.tar'.format(**env)
        env.tar_gz_basename = '{tar_basename}.gz'.format(**env)
        env.tar_pathname = os.path.join(
            env.site_build_dir, env.tar_basename)
        env.tar_gz_pathname = os.path.join(
            env.site_build_dir, env.tar_gz_basename)


@task
def init_host():
    """Initialise directory structure on target host"""
    project_dir = '/var/www/{host}'.format(**env)
    release_dir = '{project_dir}/releases'.format(project_dir=project_dir)
    data_dir = '/var/lib/www/{host}'.format(**env)

    sudo('mkdir -p {release_dir}/'.format(release_dir=release_dir))
    sudo('chown -R www-data:www-data {project_dir}'.format(
        project_dir=project_dir))

    sudo('mkdir -p {data_dir}/files'.format(data_dir=data_dir))
    sudo('chown -R www-data:www-data {data_dir}'.format(data_dir=data_dir))

    warn('Database must be manually created.')
    warn('{data_dir}/settings.php must be manually created.'.format(
        data_dir=data_dir))
    warn('{data_dir}/services.yml must be manually created.'.format(
        data_dir=data_dir))


@task
def init_deploy(tag):
    """Deploy initial release
    :param tag: Git tags from which to build release
    :type tag: str
    """
    build_exists(tag)
    build(tag)
    upload()
    extract()
    symlink_settings()
    symlink_release()
    run_drush('state-set system.maintenance 1')
    run_drush('config-import deploy')
    run_drush('updatedb')
    run_drush('cache-rebuild')
    run_drush('state-set system.maintenance 0')


@task
def deploy(tag):
    """Deploy release
    :param tag: Git tags from which to build release
    :type tag: str
    """
    build_exists(tag)
    build(tag)
    upload()
    extract()
    run_drush('state-set system.maintenance 1')
    symlink_settings()
    symlink_release()
    run_drush('config-import deploy')
    run_drush('updatedb')
    run_drush('cache-rebuild')
    run_drush('state-set system.maintenance 0')


def make_dirs(directory):
    """Create directories recursively
    :param directory: Directory to be created
    :type directory: str
    """
    if not os.path.exists(directory):
        os.makedirs(directory)


def build_exists(tag):
    """Test if release has previously been deployed
    :param tag: Git tags from which to build release
    :type tag: str
    """
    project_dir = '/var/www/{host}'.format(**env)
    release_dir = '{project_dir}/releases'.format(project_dir=project_dir)

    with settings(warn_only=True):
        result = run('! test -d {release_dir}/{tag}'.format(
            release_dir=release_dir, tag=tag))
        if result.failed:
            abort(red('Release has previously been deployed.'))


@runs_once
def build(tag):
    """Build release tarball
    :param tag: Git tags from which to build release
    :type tag: str
    """
    env.release_tag = tag

    make_dirs(env.site_repo_dir)

    with lcd(env.site_repo_dir):
        with settings(warn_only=True):
            inside_work_tree = local('git rev-parse --is-inside-work-tree')

        if inside_work_tree.failed:
            local('git clone {repository} .'.format(**env))

        if local('git pull').succeeded:
            local(('git archive '
                   '--format=tar '
                   '--output={tar_pathname} '
                   '--prefix={release_tag}/ '
                   '{release_tag}').format(**env))
            local('gzip --force -9 {tar_pathname}'.format(**env))


def upload():
    """Copy tarball to target host"""
    put(env.tar_gz_pathname, env.remote_tmp_dir)


def extract():
    """Extract tarball into release directory"""
    project_dir = '/var/www/{host}'.format(**env)
    release_dir = '{project_dir}/releases'.format(project_dir=project_dir)

    with settings(sudo_user='www-data'):
        tar_cmd = ('tar '
                   '--gzip '
                   '--extract '
                   '--verbose '
                   '--file {remote_tmp_dir}/{tar_gz_basename} '
                   '--directory {release_dir}/')
        sudo(tar_cmd.format(
            remote_tmp_dir=env.remote_tmp_dir,
            tar_gz_basename=env.tar_gz_basename,
            release_dir=release_dir))

    run('rm {remote_tmp_dir}/{tar_gz_basename}'.format(**env))


def symlink_settings():
    """Symlink configuration files and variable data"""
    project_dir = '/var/www/{host}'.format(**env)
    release_dir = '{project_dir}/releases'.format(project_dir=project_dir)
    sites_default = '{release_dir}/{release_tag}/drupal/sites/default'.format(
        release_dir=release_dir,
        release_tag=env.release_tag)
    data_dir = '/var/lib/www/{host}'.format(**env)

    with settings(sudo_user='www-data'):
        files_target = '{data_dir}/files'.format(data_dir=data_dir)
        files_dir = '{sites_default}/files'.format(sites_default=sites_default)

        sudo('ln -nfs {files_target} {files_dir}'.format(
            files_target=files_target,
            files_dir=files_dir))

        settings_target = '{data_dir}/settings.php'.format(data_dir=data_dir)
        settings_file = '{sites_default}/settings.php'.format(
            sites_default=sites_default)

        sudo('ln -nfs {settings_target} {settings_file}'.format(
            settings_target=settings_target,
            settings_file=settings_file))

        services_target = '{data_dir}/services.yml'.format(data_dir=data_dir)
        services_file = '{sites_default}/services.yml'.format(
            sites_default=sites_default)

        sudo('ln -nfs {services_target} {services_file}'.format(
            services_target=services_target,
            services_file=services_file))

    sudo('chown www-data:www-data {settings_target}'.format(
        settings_target=settings_target))
    sudo('chmod 0400 {settings_target}'.format(
        settings_target=settings_target))

    sudo('chown www-data:www-data {services_target}'.format(
        services_target=services_target))
    sudo('chmod 0400 {services_target}'.format(
        services_target=services_target))


def symlink_release():
    """Symlink release"""
    release_dir = '/var/www/{host}/releases/{release_tag}'.format(**env)
    current = '/var/www/{host}/current'.format(**env)
    with settings(sudo_user='www-data'):
        sudo('ln -nfs {release_dir} {current}'.format(
            release_dir=release_dir,
            current=current))


def run_drush(command):
    """Run a Drush command
    :param command: Drush command to run
    :type command: str
    """
    with settings(sudo_user='www-data'):
        sudo('drush -u 1 -y -r {drupal_root} {command}'.format(
            drupal_root='/var/www/{host}/current/drupal'.format(**env),
            command=command))
