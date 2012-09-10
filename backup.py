#!/usr/bin/env python
#
# This script is used for snapshotting volumes.  It can be run periodically 
# on an EC2 instance you wish to back up.
#
# The config file, typically located at backup_config.json, contains information
# about the devices to snapshot and the frequency of snapshotting them.
# The script attepts to freeze the disk before snapshotting it, by using xfs_freeze.  More filesystem freezing commands could be added to the Freezer class.
#
# What do you put in the config file?
# The config file contains information about which devices to back up, when to
# back them up, and other parameters.  Here is a listing of the parameters:
#
# {
#   "Human readable name for this volume": {
#     "path": "/path/to/mount/point",
#     "hourly": max number of backups to keep from this frequency,
#     "daily": max number of backups to keep from this frequency,
#     "weekly": max number of backups to keep from this frequency,
#     "monthly": max number of backups to keep from this frequency,
#     "after_commands": (optional) array of bash commands to run after freezing,
#     "before_commands": (optional) array of bash commands to run before freezing,
#     "description": (optional) an optional description will be appended to the description of the backup voulmes
#   }
# }
#
# Here is an example backup_config.json:
# {
#   "The raid array": {
#     "path": "/mnt/raid",
#     "weekly": 6,
#     "monthly": 6,
#     "description": "Additional info to add to the description."
#   },
#   "Some mounted volume": {
#     "path": "/home/ec2-user/mydirectory",
#     "hourly": 6,
#     "daily": 30
#     "before_commands": ["echo before > /home/ec2-user/commands.txt"],
#     "after_commands": ["echo after >> /home/ec2-user/commands.txt", "echo after2 > /home/ec2-user/after2.txt"]
#   }
# }
# 
# Important:
#   A best practice generally is to test third party scripts on a test account before
#   running them on anything important.  This is especially true in this case, because
#   you will want to configure your config file and make sure it is working the way
#   you want it to.
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
import datetime
import email.mime.text
import logging
import json
import re
import smtplib
import sys

# Local Modules
import common

_BACKUP_CONFIG_FILE = "backup_config.json"
_DATETIME_FORMAT = "%Yy-%mm-%dd %Hh%Mm"
_DEBUG = True
_EMAIL_RECIPIENT = ""  # *REPLACE* Your recipient's email address goes here.
_EMAIL_SENDER = ""  # *REPLACE* Your sender's email address goes here.
_FSTAB_PATH = "/etc/fstab"
_LOG_FILE_NAME = "backup.log"
_LOG_LEVEL = logging.DEBUG
_TIMING_MAP = {
  "minutely": datetime.timedelta(minutes=1),  # Useful for testing, not intended for real use.
  "hourly": datetime.timedelta(hours=1),
  "daily": datetime.timedelta(days=1),
  "weekly": datetime.timedelta(days=7),
  "monthly": datetime.timedelta(days=30)
}
_USERNAME = "ec2-user"

logging.basicConfig(filename=_LOG_FILE_NAME, level=_LOG_LEVEL)


class Freezer(object):
  """A Freezer can freeze and unfreeze disks, keeping track of what it's
  already frozen.  If there is an exception, make sure everything is unfrozen
  by calling unfreeze_all."""

  FREEZE_COMMANDS = {
    "xfs": "sudo xfs_freeze -f _REPLACED_WITH_MOUNT_POINT"
  }

  UNFREEZE_COMMANDS = {
    "xfs": "sudo xfs_freeze -u _REPLACED_WITH_MOUNT_POINT"
  }

  def __init__(self):
    self.frozen = []

  def freeze(self, storage):
    """Freezes the disk mounted to a mount point."""
    if not (storage.file_system_type in Freezer.FREEZE_COMMANDS):
      _log("Does not know how to freeze file system type {}".format(
        storage.file_system_type))
      _log("Continuing without freezing {}".format(storage.mount_point))
      return

    _log("Freezing {}".format(storage.mount_point))
    freeze_command = Freezer.FREEZE_COMMANDS[storage.file_system_type]
    common.run_command(freeze_command.replace("_REPLACED_WITH_MOUNT_POINT",
      storage.mount_point))
    self.frozen.append(storage)

  def unfreeze(self, storage):
    """Unfreezes the disk mounted to a mount point."""
    if not (storage.file_system_type in Freezer.UNFREEZE_COMMANDS):
      _log("Does not know how to unfreeze file system type {}".format(
        storage.file_system_type))
      _log("Continuing without unfreezing {}".format(storage.mount_point))
      return

    _log("Unfreezing {}".format(storage.mount_point))
    unfreeze_command = Freezer.UNFREEZE_COMMANDS[storage.file_system_type]
    common.run_command(unfreeze_command.replace("_REPLACED_WITH_MOUNT_POINT",
      storage.mount_point))
    self.frozen.remove(storage)

  def unfreeze_all(self):
    """Unfreezes all disks that are still frozen."""
    _log("Unfreezing all")
    for storage in self.frozen:
      try:
        self.unfreeze(storage)
      except Exception, err:
        could_not_unfreeze_reason = "Could not unfreeze {} because {}".format(
          storage.mount_point, err)
        try:
          _email("Backup could not unfreeze!", could_not_unfreeze_reason)
        except Exception:
          could_not_unfreeze_reason += " (and failed to send email too!)"
        logging.error(could_not_unfreeze_reason)


class Storage(object):
  def __init__(self, devices=None, primary_device_name="", mount_point="",
    file_system_type="", is_raid=False):
    self.devices = (devices if devices else [])
    self.primary_device_name = primary_device_name
    self.mount_point = mount_point
    self.file_system_type = file_system_type
    self.is_raid = is_raid

  def __str__(self):
    return """Storage Object
    self.devices = {}
    self.primary_device_name = {}
    self.mount_point = {}
    self.file_system_type = {}
    self.is_raid = {}
    """.format(self.devices, self.primary_device_name, self.mount_point,
    self.file_system_type, self.is_raid)


def main():  # pylint: disable=R0914
  _log("Running backup script. It is now {}".format(
    datetime.datetime.now().strftime(_DATETIME_FORMAT)))

  config = _load_config()
  connection = common.connect()

  self_instance_id = common.get_self_instance_id()
  self_instance = common.get_self_instance(connection)
  self_instance_name = (self_instance.tags['Name'] if 'Name' in
                        self_instance.tags else self_instance_id)
  attached_volumes = _get_attached_volumes(connection, self_instance_id)
  mounted_storages = _get_mounted_storages()
  freezer = Freezer()

  for name, rules in config.iteritems():
    try:
      path = rules['path']
      if rules['path'] not in mounted_storages:
        raise Exception("Cannot find mount for {}".format(path))

      extra_description = (rules['description'] if 'description' in rules
                           else "")
      storage = mounted_storages[path]

      _run_before_commands(rules)

      volume_ids = []
      for device in storage.devices:
        volume = _get_volume_used_by_device(device, attached_volumes)
        if not volume:
          raise Exception("Cannot find volume attached to {}".format(
          device))
        volume_ids.append({
          "device": device,
          "volume_id": volume.id,
        })

      _log("Preparing to back up {}".format(storage.mount_point))
      freezer.freeze(storage)
      for timing_rule in _TIMING_MAP:
        if timing_rule in rules:
          snapshots = _get_snapshots(connection, self_instance_name,
            timing_rule)
          if len(snapshots):
            # Check if we already took a recent snapshot for this duration
            most_recent_dt_string = snapshots[0].tags['Backup-Datetime']
            most_recent_dt = datetime.datetime.strptime(most_recent_dt_string,
              _DATETIME_FORMAT)
            now_dt = datetime.datetime.now()
            duration_between_backups = _TIMING_MAP[timing_rule]
            if now_dt - most_recent_dt < duration_between_backups:
              _log("Not snapshotting {} / {} because it's too soon.".format(
                name, timing_rule))
              continue

          for vol_and_device in volume_ids:
            volume_id = vol_and_device['volume_id']
            device = vol_and_device['device']

            snap_datetime = datetime.datetime.now().strftime(_DATETIME_FORMAT)
            full_desc = _build_full_description(name, self_instance_id,
              snap_datetime, timing_rule, storage, device,
              extra_description)
            short_name = " ".join([self_instance_name, timing_rule,
              device.replace("/dev/", ""), snap_datetime])

            _snapshot_volume(connection, volume_id, full_desc, {
              'Name': short_name,
              'Backup-Datetime': snap_datetime,
              'Backup-Device': device,
              'Backup-Instance-Name': self_instance_name,
              'Backup-Type': timing_rule
            })

            _delete_old_snapshots(connection, snapshots, timing_rule, device,
                                  int(rules[timing_rule]))

      freezer.unfreeze(storage)

      _run_after_commands(rules)

    except Exception, err:
      _error(err)
    finally:
      freezer.unfreeze_all()


def _build_full_description(name, instance_id, snap_datetime, backup_type,
  storage, device, extra_description):
  primary_device = storage.primary_device_name
  full_desc = name
  full_desc += " instance_id={}".format(instance_id)
  full_desc += " date={}".format(snap_datetime)
  full_desc += " type={}".format(backup_type)
  full_desc += " mount_point={}".format(storage.mount_point)
  full_desc += " device={}".format(device.replace("/dev/", ""))
  full_desc += " primary_device={}".format(primary_device.replace("/dev/", ""))
  full_desc += " "
  full_desc += extra_description
  return full_desc


def _delete_old_snapshots(connection, snapshots, backup_type, device,
                          max_backups=1000000):
  """Prunes the snapshots by ensuring that there are at most max_backups
  snapshots for a given backup_type and device, by deleting snapshots."""
  assert max_backups > 0, "The number of backups must be > 0."

  # Selectively pluck the snapshots taken for this device path.
  # Note that snapshots contains ALL the snapshots for this backup_type,
  # not including the snapshots that are taken as part of the current
  # backup process.
  device_snapshots = []
  for snapshot in snapshots:
    if snapshot.tags['Backup-Device'] == device:
      device_snapshots.append(snapshot)

  _log("Checking old snapshots for {} {}".format(backup_type, device))
  while len(device_snapshots) >= max_backups:
    last_snapshot = device_snapshots.pop()
    _log("Deleting snapshot {}".format(last_snapshot.tags['Name']))
    connection.delete_snapshot(last_snapshot.id)


def _email(subject, body):
  """Sends an email to _EMAIL_RECIPIENT with useful debugging info."""
  if not _EMAIL_RECIPIENT:
    return
  if not _EMAIL_SENDER:
    return
  message_content = "Backup script message!<br/>"
  message_content += "from instance id {}<br/>--<br/><br/>".format(
    common.get_self_instance_id())
  message_content += body
  message = email.mime.text.MIMEText(message_content, "html")
  message['From'] = _EMAIL_SENDER
  message['Subject'] = subject
  message['To'] = _EMAIL_RECIPIENT
  logging.info("Sending email to {}: {}".format(_EMAIL_RECIPIENT, subject))
  smtp = smtplib.SMTP("127.0.0.1")
  smtp.sendmail(_EMAIL_SENDER, [_EMAIL_RECIPIENT], message.as_string())
  smtp.quit()


def _error(reason):
  reason = str(reason)
  try:
    _email("Backup error!", reason)
  except Exception:
    reason += " (and failed to send email too!)"
  logging.error(reason)
  sys.exit(reason)


def _get_snapshots(connection, instance_name, backup_type):
  """Gets all the snapshots taken for a particular backup type for
  this instance. This is needed because we're going to check when the
  last backup was taken, and also delete any extraneous backups."""
  filters = {
    'tag:Backup-Type': backup_type,
    #'tag:Backup-Device': device,
    'tag:Backup-Instance-Name': instance_name,
  }
  snapshots = connection.get_all_snapshots(filters=filters)
  valid_snapshots = []
  for snapshot in snapshots:
    try:
      _sort_snapshots_by_datetime(snapshot, snapshot)
      valid_snapshots.append(snapshot)
    except Exception:
      # Perhaps 'Backup-Datetime' not in snapshot.tags or
      # perhaps the snapshot was saved with a different datetime
      # format. In this case, this snapshot won't make the list.
      # One could add a warning here.
      pass
  valid_snapshots.sort(_sort_snapshots_by_datetime)
  return valid_snapshots


def _get_attached_volumes(connection, instance_id=None):
  """Returns all the volumes that are attached to a particular instance.
  Alternatively, this function could've been a generator with the
  yield keyword."""
  if not instance_id:
    instance_id = common.get_self_instance_id()

  volumes = connection.get_all_volumes()
  attached_volumes = []
  for volume in volumes:
    if (volume.attach_data.instance_id == instance_id and
      volume.attachment_state() == "attached"):
      attached_volumes.append(volume)
  return attached_volumes


def _get_mounted_storages():
  """Returns a dictionary of Storage objects, indexed by their
  mount_point.  The Storage objects are found using the fstab."""
  fstab_info = common.run_command("cat {}".format(_FSTAB_PATH)).split("\n")
  storages = {}
  for line in fstab_info:
    trimmed_line = line.strip()
    if not trimmed_line or trimmed_line[0] == "#":
      continue
    if trimmed_line.find("LABEL=") == 0:
      # The first line is typically the table header.
      continue

    storage = Storage()
    matched_fs_info = re.search("^\s*(\S+)\s+(\S+)\s+(\S+)", trimmed_line)
    if matched_fs_info:
      storage.primary_device_name = matched_fs_info.group(1)
      storage.mount_point = matched_fs_info.group(2)
      storage.file_system_type = matched_fs_info.group(3)

    # Now check if this is a RAID array
    mdadms = common.run_command("sudo mdadm --detail --scan")
    if "ARRAY {}".format(storage.primary_device_name) in mdadms:
      storage.is_raid = True
      # Now find a full listing of ALL the devices used
      raid_device_info = common.run_command("sudo mdadm --detail {}".format(
        storage.primary_device_name)).split("\n")
      for line in raid_device_info:
        # We are pulling the devices off of the lines that look like this:
        #   0     202       97        0      active sync   /dev/sdg1
        matched_raid_device_info = re.search(
          "\s*\d+?\s+\d+?\s+\d+\s+\d+.*?(/dev/.+)", line)
        if matched_raid_device_info:
          storage.devices.append(matched_raid_device_info.group(1).strip())
    else:
      storage.is_raid = False
      storage.devices.append(storage.primary_device_name)

    storages[storage.mount_point] = (storage)
    # End for loop

  if _DEBUG:
    for path, storage in storages.iteritems():
      if path and path != "none":
        _log(storage)

  return storages


def _get_volume_used_by_device(device, volumes):
  """This function plunks a volume ids out of a list of
  volumes, when the volume is using the device."""
  _log("Getting volume id used by {}".format(device))
  for volume in volumes:
    if volume.attach_data.device == device:
      return volume


def _load_config():
  try:
    with open(_BACKUP_CONFIG_FILE, "r") as backup_config_file:
      return json.load(backup_config_file)
  except Exception:
    _error("Could not load backup config file {}".format(_BACKUP_CONFIG_FILE))


def _log(message):
  logging.info(message)


def _run_commands(commands):
  for command in commands:
    _log("Running custom command {}".format(command))
    common.run_command(command)


def _run_after_commands(rules):
  _log("Running any after commands specified in the config")
  after_commands = (rules['after_commands'] if 'after_commands' in rules
                    else [])
  _run_commands(after_commands)


def _run_before_commands(rules):
  _log("Running any before commands specified in the config")
  before_commands = (rules['before_commands'] if 'before_commands' in rules
                     else [])
  _run_commands(before_commands)


def _snapshot_volume(connection, volume_id, description, tags):
  """Snapshots the volume given by volume_id, with a description."""
  snapshot = connection.create_snapshot(volume_id, description)
  if not snapshot:
    raise Exception("Error taking snapshot for volume {}".format(volume_id))

  # Might want to wait here for the snapshotting to finish
  # could probably keep checking snapshot.???
  connection.create_tags([snapshot.id], tags)

  _log("Snapshot taken of volume {}".format(volume_id))
  return snapshot


def _sort_snapshots_by_datetime(snapshot1, snapshot2):
  """Given two snapshots, returns the difference of their times.
  This can be used to sort an array of snapshots."""
  datetime_str1 = snapshot1.tags['Backup-Datetime']
  datetime_str2 = snapshot2.tags['Backup-Datetime']
  datetime1 = datetime.datetime.strptime(datetime_str1, _DATETIME_FORMAT)
  datetime2 = datetime.datetime.strptime(datetime_str2, _DATETIME_FORMAT)
  if datetime1 < datetime2:
    return 1
  elif datetime1 == datetime2:
    return 0
  else:
    return -1


if __name__ == "__main__":
  main()
