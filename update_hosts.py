#!/usr/bin/env python

# This script will update your /etc/hosts file with information about all your
# EC2 instances. It is useful to alias your EC2 instances by the human readable
# name you give them in EC2. This way, instead of connecting to an ip address,
# you can connect to a human readable name like "redis-1c-1".
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
from StringIO import StringIO
import re
import subprocess
import tempfile

# Local Modules
import common


_HOSTS_PATH = "/etc/hosts"


def main():
  connection = common.connect()

  prefix = raw_input(
    "Do you want the section of {} to have a prefix? (default '')".format(
      _HOSTS_PATH))
  if prefix:
    prefix = prefix + " "
  region = common.prompt_region(connection)
  type_ = common.prompt_choice("Type", ["private", "public"], "public")

  begin_marker = "# {}{} begin".format(prefix, region.name)
  end_marker = "# {}{} end".format(prefix, region.name)

  with open(_HOSTS_PATH) as hosts_file:
    content = hosts_file.read()

  content = re.sub("\s*{}.*{}".format(begin_marker, end_marker), "", content,
                   flags=re.DOTALL)

  connection = common.connect(region)
  mapping = sorted(_get_mapping(connection, type_ == "public"))
  content += _generate_mapping_content(begin_marker, end_marker, mapping)

  with tempfile.NamedTemporaryFile(delete=False) as temporary_file:
    temporary_file.write(content)

  subprocess.check_call([
    "sudo",
    "cp",
    temporary_file.name,
    _HOSTS_PATH
  ])


def _generate_mapping_content(begin_marker, end_marker, mapping):
  output = StringIO()
  output.write("\n")
  output.write(begin_marker)
  output.write("\n")

  for name, ip_address in mapping:
    output.write("{:15} {}\n".format(ip_address, name))

  output.write(end_marker)
  output.write("\n")

  return output.getvalue()


def _get_mapping(connection, public):
  for reservation in connection.get_all_instances():
    instance = reservation.instances[0]
    ip_address = instance.ip_address if public else instance.private_ip_address
    name = instance.tags.get("Name")

    if ip_address and name:
      yield name, ip_address


if __name__ == "__main__":
  main()
