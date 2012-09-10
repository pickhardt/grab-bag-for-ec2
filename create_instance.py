#!/usr/bin/env python
#
# This script creates a new instance on Amazon EC2. We used it by creating an instance,
# loading basic Yum packages, creating an ephemeral RAID, and cloning our code from Github.
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
from collections import namedtuple
import os
import sys
import time

# Third-Party Modules
from boto.ec2.blockdevicemapping import BlockDeviceMapping
from boto.ec2.blockdevicemapping import BlockDeviceType
from boto.exception import EC2ResponseError
from fabric.api import env, prompt, put, reboot, run, sudo

# Local Modules
import common


# http://aws.amazon.com/amazon-linux-ami
# http://aws.amazon.com/amazon-linux-ami/latest-release-notes
_AMI = {
  'us-east-1': "ami-aecd60c7",  # 64-bit Amazon Linux 2012.03.3 (EBS-Backed)
  'us-west-2': "ami-48da5578"  # 64-bit Amazon Linux 2012.03.3 (EBS-Backed)
}

_DEFAULT_INSTANCE_TYPE = "m1.large"
_GIT_REPO = ""  #  *REPLACE* Replace with your git repo, such as git@github.com:YOURNAME/YOURFILE.git
_USERNAME = "ec2-user"
_WAIT_FOR_REMOTE_INTERVAL = 10
_WAIT_FOR_START_INTERVAL = 3

_InstanceType = namedtuple("_InstanceType", [
  "name",
  "ephemeral_disk_count"
])

_LaunchArguments = namedtuple("_LaunchArguments", [
  "instance_type",
  "key_name",
  "name",
  "security_group",
  "zone"
])

_GEM_PACKAGES = [
  "rake"
]

# http://docs.amazonwebservices.com/AWSEC2/latest/UserGuide/instance-types.html
_INSTANCE_TYPES = [
  _InstanceType("m1.small", 1),
  _InstanceType("m1.large", 2),
  _InstanceType("m1.xlarge", 4),
  _InstanceType("t1.micro", 0),
  _InstanceType("c1.medium", 1),
  _InstanceType("c1.xlarge", 4),
  _InstanceType("m2.xlarge", 1),
  _InstanceType("m2.2xlarge", 1),
  _InstanceType("m2.4xlarge", 2),
  _InstanceType("hi1.4xlarge", 2),
  _InstanceType("cc1.4xlarge", 2),
  _InstanceType("cc2.8xlarge", 4),
  _InstanceType("cg1.4xlarge", 2)
]

_YUM_PACKAGES = [
  "bzip2-devel",
  "gcc-c++",
  "git",
  "hdparm",
  "httpd-tools",
  "libxml2-devel",
  "libxslt",
  "libxslt-devel",
  "make",
  "ncurses-devel",
  "openssl-devel",
  "php-fpm",
  "rubygems",
  "screen",
  "sysstat",
  "telnet",
  "xfsprogs"
]


def main():
  connection = common.connect()
  region = common.prompt_region(connection)
  connection = common.connect(region)
  zone = common.prompt_zone(connection)
  security_group = common.prompt_security_group(connection)
  prefix = "{}-{}-".format(security_group, zone.split("-")[-1])
  name = _prompt_name(connection, prefix)
  instance_type = _prompt_instance_type()
  key_path = common.prompt_key_path()
  key_name = os.path.basename(key_path).split(".")[0]

  arguments = _LaunchArguments(instance_type=instance_type,
                               key_name=key_name,
                               name=name,
                               security_group=security_group,
                               zone=zone)

  env.host_string = _launch(connection, arguments, region)
  env.key_filename = key_path
  env.user = _USERNAME
  common.wait_until_remote_reachable()
  sudo("hostname {}".format(name))
  _update_system_files(name)
  _install()
  _update_installed_files()
  reboot()

  if instance_type.ephemeral_disk_count > 1:
    _create_ephemeral_raid(instance_type.ephemeral_disk_count)
  
  if _GIT_REPO:
    _clone()


def _clone():
  """Clones a git repository."""
  put("id_rsa", ".ssh/id_rsa")
  run("chmod 400 .ssh/id_rsa")
  put("known_hosts", ".ssh/known_hosts")
  run("git clone {}".format(_GIT_REPO))


def _create_device_map(ephemeral_disk_count):
  """Creates a block device out of the ephemeral disks on this instance."""
  device_map = BlockDeviceMapping()
  device_paths = _get_device_paths(ephemeral_disk_count)

  for index, device_path in enumerate(device_paths):
    device = BlockDeviceType()
    device.ephemeral_name = "ephemeral{}".format(index)
    device_map[device_path] = device

  return device_map


def _create_ephemeral_raid(ephemeral_disk_count, raid_device_path="/dev/md0",
  raid_directory_path="/mnt/ephemeral"):
  """Creates a RAID array out of the ephemeral disks, using device path
  raid_device_path and mounted at mount point raid_directory_path.
  This function generally follows the method outlined here:
  http://www.gabrielweinberg.com/blog/2011/05/raid0-ephemeral-storage-on-aws-ec2.html"""  # pylint: disable=C0301
  device_paths = _get_device_paths(ephemeral_disk_count)
  device_paths_string = " ".join(device_paths)

  mdadm_parameters = [
    "--create {}".format(raid_device_path),
    "--chunk=256",
    "--level=0",
    "--raid-devices={}".format(len(device_paths)),
    device_paths_string
  ]

  sudo("umount /dev/sdb || true")
  sudo("yes | mdadm {}".format(" ".join(mdadm_parameters)))
  sudo("echo 'DEVICE {}' >> /etc/mdadm.conf".format(device_paths_string))
  sudo("mdadm --detail --scan >> /etc/mdadm.conf")
  sudo("blockdev --setra 65536 {}".format(raid_device_path))
  sudo("mkfs.xfs -f {}".format(raid_device_path))
  sudo("mkdir -p {}".format(raid_directory_path))

  fstab_line_parts = [
    raid_device_path,
    raid_directory_path,
    "xfs",
    "defaults,noatime",
    "0",
    "0"
  ]

  sudo("echo '{}' >> /etc/fstab".format(" ".join(fstab_line_parts)))
  sudo("mount {}".format(raid_device_path))
  sudo("chown ec2-user:ec2-user {}".format(raid_directory_path))

  # Manually assembles RAID array.  This should be done automatically at boot,
  # but that doesn't seem to happen.
  sudo("echo '/sbin/mdadm -A {}' >> /etc/rc.local".format(raid_device_path))
  sudo("echo '/bin/mount {}' >> /etc/rc.local".format(raid_device_path))
  sudo("echo '/sbin/blockdev --setra 65536 {}' >> /etc/rc.local".format(
    raid_device_path))  # ensures blockdev runs on every start up
  sudo("dracut --force")


def _get_device_paths(count):
  """Returns a list of device paths. The device paths are ordered
  alphabetically starting with /dev/sdb, followed by /dev/sdc and /dev/sdd."""
  device_paths = []

  for index in range(count):
    character = chr(ord("b") + index)
    device_paths.append("/dev/sd{}".format(character))

  return device_paths


def _install():
  """Installs important yum and gem packages."""
  sudo("yum update --assumeyes")
  sudo("yum install {} --assumeyes".format(" ".join(_YUM_PACKAGES)))
  sudo("gem install {}".format(" ".join(_GEM_PACKAGES)))


def _launch(connection, arguments, region):
  """Launches a new instance in region."""
  print("Launching {}".format(arguments))
  instance_type = arguments.instance_type
  device_map = _create_device_map(instance_type.ephemeral_disk_count)
  reservation = connection.run_instances(_AMI[region.name],
                                         block_device_map=device_map,
                                         instance_type=instance_type.name,
                                         key_name=arguments.key_name,
                                         monitoring_enabled=True,
                                         placement=arguments.zone,
                                         security_groups=[
                                           arguments.security_group
                                         ])
  instance = reservation.instances[0]

  for attempt in range(1, 4):
    try:
      connection.create_tags([instance.id], {
        'Name': arguments.name
      })

      break
    except EC2ResponseError:
      print("Create name tag failed, sleeping (attempt={})".format(attempt))
      time.sleep(1)

  print("Waiting for instance to start")
  status = instance.update()

  while status == "pending":
    time.sleep(_WAIT_FOR_START_INTERVAL)
    status = instance.update()

  if status == "running":
    print("Launched {} at {}".format(instance.id, instance.public_dns_name))
  else:
    sys.exit("Not running: {}".format(status))

  return instance.public_dns_name


def _prompt_instance_type():
  """Prompts for an instance type."""
  instance_types = [(instance_type.name, instance_type)
                    for instance_type in _INSTANCE_TYPES]

  return common.prompt_choice("Instance type", instance_types,
                              _DEFAULT_INSTANCE_TYPE)


def _prompt_name(connection, prefix):
  """Prompts for a name for the new instance. This prompt function will
  ask to use a smart default given by the prefix provided plus the next
  available number unused by any existing instances over the connection.
  For example, if prefix is 'redis' and you already have 'redis1' then
  the suggested default will be 'redis2'."""
  instances = []
  highest_number = 0

  for reservation in connection.get_all_instances():
    instance = reservation.instances[0]
    name = instance.tags.get("Name")

    if name and name.startswith(prefix):
      instances.append((name, instance))

      try:
        number = int(name[len(prefix)])
        highest_number = max(highest_number, number)
      except ValueError:
        pass

  for name, instance in sorted(instances):
    print("{}: {}".format(name, instance.state))

  return prompt("Name?", default="{}{}".format(prefix, highest_number + 1))


def _update_installed_files():
  """Updates the php-fpm files."""
  php_fpm_path = "/etc/php-fpm.d/www.conf"
  sudo("sed 's/= apache/= ec2-user/' {} > php-fpm".format(php_fpm_path))
  sudo("mv php-fpm {}".format(php_fpm_path))
  sudo("chkconfig php-fpm on")


def _update_system_files(name):
  """Updates the system files with config info for the RAID."""
  sudo("sed '/ephemeral/d' /etc/fstab > fstab")
  sudo("mv fstab /etc/fstab")
  sudo("echo '127.0.0.1   {}' >> /etc/hosts".format(name))
  sudo("echo /bin/hostname {} >> /etc/rc.local".format(name))
  sudo("echo fs.file-max = 2097152 >> /etc/sysctl.conf")
  sudo("echo net.core.somaxconn = 65536 >> /etc/sysctl.conf")
  sudo("echo vm.overcommit_memory = 1 >> /etc/sysctl.conf")
  sudo("echo '* hard nofile 131072' >> /etc/security/limits.conf")
  sudo("echo '* soft nofile 131072' >> /etc/security/limits.conf")


if __name__ == "__main__":
  main()
