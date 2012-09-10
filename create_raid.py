#!/usr/bin/env python
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
import re
import sys
import time

# Third-Party Modules
from fabric.api import env, prompt, sudo

# Local Modules
import common


_DEFAULT_LEVEL = "0"
_DELAY_FOR_VOLUMES_TO_ATTACH = 4  # seconds
_USERNAME = "ec2-user"

# http://en.wikipedia.org/wiki/Mdadm#RAID_Configurations
_LEVELS = [
  "0",
  "1",
  "10",
  "4",
  "5",
  "6",
  "container",
  "faulty",
  "multipath"
]


def main():
  connection = common.connect()

  instance = common.prompt_instance(connection,
    "Which instance to create a RAID for")
  if not instance:
    sys.exit("Instance not found.")

  key_path = common.get_pem(instance)
  print("Using key: {}".format(key_path))

  env.host_string = instance.public_dns_name
  env.key_filename = key_path
  env.user = _USERNAME

  number_of_disks = common.prompt_choice(
    "How many EBS volumes should be in the RAID array", range(1, 11), 4)
  size_of_disks = _prompt_size()
  level = common.prompt_choice("Level", _LEVELS, _DEFAULT_LEVEL)

  common.wait_until_remote_reachable()

  possible_device_paths = ["/dev/sd" + chr(letter)
    for letter in range(ord("f"), ord("z") + 1)]

  # Remove used device paths from the list
  used_devices = _get_existing_device_paths()

  for used_device in used_devices:
    used_device_no_trailing_digits = re.sub("\d+$", "", used_device)
    if used_device_no_trailing_digits in possible_device_paths:
      possible_device_paths.remove(used_device_no_trailing_digits)

  # Offer the user a prompt of which device path to beginning with
  prefix_device_path = common.prompt_choice(
    "Volumes should be attached with numbers appended to which device path",
    possible_device_paths[:9], 1)

  # Create the disks
  new_volume_device_strings = []
  for index in range(number_of_disks):
    device_string = prefix_device_path + str(index + 1)  # example: /dev/sdf2
    new_volume_device_strings.append(device_string)
    _create_ebs_volume(connection, instance, size_of_disks, device_string)

  # Figure out where to attach the new RAID
  possible_devices_for_raid = ["/dev/md" + str(index) for index in range(10)]
  for used_device in used_devices:
    if used_device in possible_devices_for_raid:
      possible_devices_for_raid.remove(used_device)
  raid_device_path = common.prompt_choice(
      "The RAID should be built at which device path",
        possible_devices_for_raid[:5], 1)

  print("Where do you want the RAID directory to be? [Default: /mnt/raid]")
  raid_directory_path = raw_input()
  if raid_directory_path == "":
    raid_directory_path = "/mnt/raid"

  _create_raid(new_volume_device_strings, level, raid_device_path,
               raid_directory_path)


def _create_ebs_volume(connection, instance, size_of_volume, device_path):
  """Creates an EBS volume of size size_of_volume, attaches it to instance at
  the device path device_path."""
  instance_name = instance.tags.get("Name")
  volume_name = "{}-{}".format(instance_name, device_path.replace("/dev/", ""))
  print("Creating EBS volume {} and attaching to {}".format(volume_name,
    device_path))
  new_volume = connection.create_volume(size_of_volume, instance.placement)

  new_volume.attach(instance.id, device_path)
  print("Ensuring {} is found as a device".format(device_path))
  attempt = 0
  while True:
    attempt += 1
    if device_path in _get_existing_device_paths():
      break
    else:
      print("Couldn't find {} (attempt={})".format(device_path, attempt))
      print("Sleeping {} seconds".format(_DELAY_FOR_VOLUMES_TO_ATTACH))
      time.sleep(_DELAY_FOR_VOLUMES_TO_ATTACH)
  print("Successfully found device attached.")

  connection.create_tags([new_volume.id], {"Name": volume_name})


# Following this tutorial:
# http://www.gabrielweinberg.com/blog/2011/05/raid0-ephemeral-storage-on-aws-ec2.html pylint: disable=C0301
def _create_raid(device_paths, level, raid_device_path, raid_directory_path):
  """Creates a RAID array made up of the storage found at device_paths."""
  print("Creating RAID array attached to {} with directory {}".format(
raid_device_path, raid_directory_path))

  device_paths_string = " ".join(device_paths)

  # Ensure yum packages are available (needed for mkfs.xfs command)
  sudo("yum install xfsprogs --assumeyes")

  # Create RAID array
  mdadm_parameters = [
    "--create {}".format(raid_device_path),
    "--chunk=256",
    "--level={}".format(level),
    "--raid-devices={}".format(len(device_paths)),
    device_paths_string
  ]
  sudo("yes | mdadm {}".format(" ".join(mdadm_parameters)))
  sudo("echo 'DEVICE {}' >> /etc/mdadm.conf".format(device_paths_string))
  sudo("mdadm --detail --scan | grep {} >> /etc/mdadm.conf".format(raid_device_path))
  sudo("blockdev --setra 65536 {}".format(raid_device_path))

  # Format with XFS file system
  sudo("mkfs.xfs -f {}".format(raid_device_path))

  # Make a directory and add it as the mount point
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
    raid_device_path))


def _get_existing_device_paths():
  ls_devices = re.findall(r'\w+', sudo("ls /dev"))
  return ["/dev/" + device for device in ls_devices if device.strip() != ""]


def _prompt_size():
  def validate(value):
    try:
      value = int(value)
      assert 1 <= value <= 1000

      return value
    except (AssertionError, ValueError):
      raise Exception("Volume size must be between 1 and 1000.")

  size = prompt("Volume size (GB) [1-1000]?", default="10", validate=validate)
  print("Chose {}".format(size))

  return size


if __name__ == "__main__":
  main()
