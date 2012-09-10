#!/usr/bin/env python
#
# This file contains common functions that can be used by the other scripts.
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
import os
import sys
import time

# Third-Party Modules
from boto.ec2.connection import EC2Connection
from boto.ec2 import elb
from fabric.api import hide, prompt, run
import fabric.exceptions

# AWS Credentials
from credentials import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

_DEFAULT_KEY = "YOUR_PEM_FILE_NAME.pem"  # *REPLACE* with your pem file.
_DEFAULT_REGION = "us-east-1"
_DEFAULT_SECURITY_GROUP = "default"
_DEFAULT_ZONE = "us-east-1c"
_KEY_DIRECTORY_PATH = os.path.expanduser("~/.ssh")
_WAIT_FOR_REMOTE_INTERVAL = 10


def connect(region=None):
  """Connects to EC2 and returns an EC2Connection."""
  return EC2Connection(aws_access_key_id=AWS_ACCESS_KEY_ID,
                       aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                       region=region)


def connect_elb_region(region_name=_DEFAULT_REGION):
  """Connects to the ELB service for region_name and returns the ELB
  connection."""
  print ("Connecting to ELB service for region {}".format(region_name))
  return elb.connect_to_region(region_name,
                               aws_access_key_id=AWS_ACCESS_KEY_ID,
                               aws_secret_access_key=AWS_SECRET_ACCESS_KEY)


def get_self_instance_id():
  """Returns the instance id of the instance this is running on."""
  # See also:
  # http://stackoverflow.com/questions/625644
  # http://docs.amazonwebservices.com/AWSEC2/latest/UserGuide/AESDG-chapter-instancedata.html pylint: disable=C0301
  return run_command("wget -q -O - http://169.254.169.254/latest/meta-data/instance-id")


def get_self_instance(connection):
  """Returns an instance corresponding to the server that this script is
  running on, by using get_self_instance_id."""
  instance_id = get_self_instance_id()
  filters = {'instance-id': instance_id}
  reservations = connection.get_all_instances(filters=filters)
  return reservations[0].instances[0]


def get_pem(instance):
  """Returns the .pem file needed to communicate with a given instance. This
  does not guarantee that the .pem file exists."""
  return "{}/{}.pem".format(_KEY_DIRECTORY_PATH, instance.key_name)


def prompt_choice(question, choices, default=None):
  """Prompts the user for a choice amongst choices.  default is the default
  index of the choices that will be used if the user doesn't type anything in.
  The question parameter is prompted to the user."""
  keys = sorted(choices)
  values = keys

  if isinstance(choices[0], tuple):
    keys = []
    values = []

    for key, value in sorted(choices):
      keys.append(key)
      values.append(value)

  default_number = "1"
  numbers = [str(number) for number in range(1, len(keys) + 1)]

  for index, key in enumerate(keys):
    number = numbers[index]
    print("{}. {}".format(number, key), end="")

    if key != default:
      print()
    else:
      default_number = number
      print(" (default)")

  message = "{}?".format(question)
  validate = "|".join(numbers)

  try:
    number = prompt(message, default=default_number, validate=validate)
  except KeyboardInterrupt:
    sys.exit(0)

  index = int(number) - 1
  print("Chose {}".format(keys[index]))

  return values[index]


def prompt_confirmation(confirmation, default_choice=False):
  """Prompts the user to confirm whether they want to do something."""
  while True:
    answer = raw_input("{}? (y/n, default {})  ".format(confirmation,
      ("y" if default_choice else "n")))
    answer = answer.lower()
    if answer == "y":
      return True
    elif answer == "n":
      return False
    elif answer == "":
      return default_choice
    # If it is neither a y, n, or blank, prompt again!


def prompt_elb(elb_connection, default_elb_name):
  """Prompts the user to choose an elastic load balancer."""
  elbs = [(some_elb.name, some_elb) for some_elb
          in elb_connection.get_all_load_balancers()]
  if elbs:
    return prompt_choice("ELB", elbs, default_elb_name)
  else:
    return None


def prompt_instance(connection, description):
  """Prompts the user to choose an instance amongst all the available
  instances found on the connection. Description is prompted to the user
  as the prompt."""
  names = []
  instances = []
  for reservation in connection.get_all_instances():
    instance = reservation.instances[0]
    instances.append(instance)
    names.append(instance.tags.get("Name"))
  chosen_name = prompt_choice(description, names)
  return instances[names.index(chosen_name)]


def prompt_key_path():
  """Prompts the user to choose a .pem key. Returns the path to the
  .pem file."""
  keys = [key for key in os.listdir(_KEY_DIRECTORY_PATH)
          if key.endswith(".pem")]
  key = prompt_choice("Key", keys, _DEFAULT_KEY)

  return os.path.join(_KEY_DIRECTORY_PATH, key)


def prompt_region(connection):
  """Prompts the user to choose an AWS region."""
  regions = [(region.name, region) for region in connection.get_all_regions()]

  return prompt_choice("Region", regions, _DEFAULT_REGION)


def prompt_security_group(connection, default_group=_DEFAULT_SECURITY_GROUP):
  """Prompts for a security group with a default of default_group"""
  groups = [group.name for group in connection.get_all_security_groups()]

  return prompt_choice("Security group", groups, default_group)


def prompt_zone(connection, default_zone=_DEFAULT_ZONE):
  """Prompts for a zone."""
  zones = [zone.name for zone in connection.get_all_zones()]

  return prompt_choice("Zone", zones, default_zone)


def run_command(command):
  """Shim for running commands on the local operating system.
  This allows us to use either subprocess.check_output
  (Python 2.7+) or commands (before 2.7)."""
  try:
    import commands
    return commands.getoutput(command)
  except ImportError:
    try:
      # check_output was only used in Python 2.7+.
      from subprocess import check_output as subprocess_check_output
      command_arguments = command.split(" ")
      return str(subprocess_check_output(command_arguments))
    except ImportError:
      raise ImportError(
        "Neither modules 'commands' nor 'subprocess' were found.")


def wait_until_remote_reachable():
  """Waits until the remote host (Fabric environment settings) is reachable."""
  print("Ensuring remote host is reachable")
  attempt = 0

  while True:
    try:
      attempt += 1

      with hide("running"):
        run("echo")

      break
    except fabric.exceptions.NetworkError:
      print("Waiting for remote host (attempt={})".format(attempt))
      print("Sleeping {} seconds".format(_WAIT_FOR_REMOTE_INTERVAL))
      time.sleep(_WAIT_FOR_REMOTE_INTERVAL)

  print("Successfully reached remote host")
