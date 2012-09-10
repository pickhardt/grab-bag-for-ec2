#!/usr/bin/env python
#
# NO WARRANTY
#
# THE PROGRAM IS DISTRIBUTED IN THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY. IT IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU. SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING, REPAIR OR CORRECTION.
#
# IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW THE AUTHOR WILL BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS), EVEN IF THE AUTHOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

from __future__ import print_function

# Standard Modules
from collections import namedtuple

# Local Modules
import common


_DEFAULT_ELB_NAME = "default-elb-name-goes-here"
_DEFAULT_ELB_ACTION = "Show Info"
_ELB_ACTIONS = ["Show Info",
                "Add Instance",
                "Remove Instance",
                #"Check Instance",
                "Exit"
]

_ELBInfo = namedtuple("_ELBInfo", [
  "zones",
  "registered_instances",
  "unregistered_instances"
])

_InstanceInfo = namedtuple("_InstanceInfo", [
  "name",
  "id",
  "status",
  "zone"
])


def main():
  ec2_connection = common.connect()
  region = common.prompt_region(ec2_connection)
  elb_connection = common.connect_elb_region(region.name)
  elb = common.prompt_elb(elb_connection, _DEFAULT_ELB_NAME)

  if not elb:
    print("No ELBs exist for region {}".format(region.name))
    return

  choice = common.prompt_choice("Choice", _ELB_ACTIONS, _DEFAULT_ELB_ACTION)
  while (choice != "Exit"):
    elb_info = _get_elb_info(ec2_connection, elb)
    _handle_user_choice(choice, elb, elb_info.zones,
                        elb_info.registered_instances,
                        elb_info.unregistered_instances)
    choice = common.prompt_choice("Choice", _ELB_ACTIONS, _DEFAULT_ELB_ACTION)
  return


def _add_instance(elb, elb_zones, unregistered_instances):
  """Prompts the user for an instance to add, then adds that instance
  to an ELB."""
  _pretty_print_elb_zones(elb, elb_zones)
  _pretty_print_elb_instances(elb, unregistered_instances, False)

  unreg_instances = [(inst.name, inst) for inst in unregistered_instances]
  instance_to_add = common.prompt_choice("Add", unreg_instances)
  if common.prompt_confirmation("Are you sure you want to add {}".format(
    instance_to_add.name)):
    elb.register_instances([instance_to_add.id])
    print("Instance added to ELB.")
  else:
    print("Instance was NOT added to ELB.")


def _get_elb_info(ec2_connection, elb):
  """Gets general info about the ELB and returns an
  _ELBInfo namedtuple."""
  zones = ec2_connection.get_all_zones(elb.availability_zones)
  elb_zones = {zone.name: {'instance_count': 0, 'status': zone.state}
               for zone in zones}

  registered_instances = []
  unregistered_instances = []
  elb_instances = elb.connection.describe_instance_health(elb.name)
  elb_instance_ids = [instance.instance_id for instance in elb_instances]
  reservations = ec2_connection.get_all_instances()
  for reservation in reservations:
    for instance in reservation.instances:
      inst_info = _InstanceInfo(instance.tags.get("Name"), instance.id,
                                instance.state, instance.placement)
      if inst_info.id in elb_instance_ids:
        registered_instances.append(inst_info)
        if inst_info.zone in elb_zones:
          elb_zones[inst_info.zone]["instance_count"] += 1
        else:
          print("""*** Warning! ELB has an out-of-zone instance.
***   Instance name: {}
***   Instance zone: {}""".format(inst_info.name, inst_info.zone))
      else:
        unregistered_instances.append(inst_info)

  registered_instances = sorted(registered_instances)
  unregistered_instances = sorted(unregistered_instances)
  return _ELBInfo(elb_zones, registered_instances, unregistered_instances)


def _handle_user_choice(choice, elb, elb_zones,
                        registered_instances, unregistered_instances):
  """Handles the user choice in the main read-eval-print loop."""
  if choice == "Show Info":
    _show_info(elb, elb_zones, registered_instances,
                      unregistered_instances)
  elif choice == "Remove Instance":
    _remove_instance(elb, registered_instances)
  elif choice == "Add Instance":
    _add_instance(elb, elb_zones, unregistered_instances)


def _pretty_print_elb_zones(elb, elb_zones):
  """Prints the available ELB zones in a pretty tabular view."""
  template = "Availability Zones for ELB {}: {}"
  print(template.format(elb.name, len(elb_zones)))

  template = "{:16} {!s:16} {:12}"
  print(template.format("ZONE", "INSTANCE COUNT", "STATUS"))

  zones = sorted(elb_zones, key=elb_zones.get)
  for zone in zones:
    print(template.format(zone, elb_zones[zone]["instance_count"],
                                elb_zones[zone]["status"]))
  print()


def _pretty_print_elb_instances(elb, instances, are_registered):
  """Prints the instances registered or unregistered for this instance
  in a pretty tabular view."""
  template = "R" if are_registered else "Unr"
  template += "egistered instances for ELB {}: {}"
  print(template.format(elb.name, len(instances)))

  template = "{:22} {:16} {:12} {:16}"
  print(template.format("NAME", "ID", "STATUS", "ZONE"))

  for inst in instances:
    print(template.format(inst.name, inst.id, inst.status, inst.zone))
  print()


def _remove_instance(elb, registered_instances):
  """Prompts the user for an instance to remove from the ELB, then removes
  it. Asks for a confirmation before actually removing it."""
  _pretty_print_elb_instances(elb, registered_instances, True)

  reg_instances = [(inst.name, inst) for inst in registered_instances]
  if len(reg_instances) == 0:
    print("Cannot remove an instance since none are registered to this ELB.")
    return

  instance_to_remove = common.prompt_choice("Remove", reg_instances)
  if common.prompt_confirmation("Are you sure you want to remove {}".format(
    instance_to_remove.name)):
    elb.deregister_instances([instance_to_remove.id])
    print("Instance removed from ELB.")
  else:
    print("Instance was NOT removed from ELB.")


def _show_info(elb, elb_zones, registered_hosts, unregistered_hosts):
  """Shows general info about the ELB."""
  print("ELB Info")
  print("ELB Name: {}".format(elb.name))
  print("ELB DNS Name: {}".format(elb.dns_name))
  _pretty_print_elb_zones(elb, elb_zones)
  _pretty_print_elb_instances(elb, registered_hosts, True)
  _pretty_print_elb_instances(elb, unregistered_hosts, False)


if __name__ == "__main__":
  main()
