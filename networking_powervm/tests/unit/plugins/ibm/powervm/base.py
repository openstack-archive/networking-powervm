# Copyright IBM Corp. and contributors
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import fixtures
import mock
from neutron.tests import base
from pypowervm.tasks import partition as pvm_par

from networking_powervm.plugins.ibm.agent.powervm import prov_req


def mk_preq(action, mac, segment_id=None, phys_network=None,
            lpar_uuid='lpar_uuid', vif_type=None):
    device = {'mac_address': mac, 'physical_network': phys_network,
              'segmentation_id': segment_id}
    return prov_req.ProvisionRequest(action, device, lpar_uuid, vif_type)


class AgentFx(fixtures.Fixture):
    def setUp(self):
        super(AgentFx, self).setUp()
        # For agent init
        self.adpt = self.useFixture(fixtures.MockPatch(
            'pypowervm.adapter.Adapter')).mock
        self.sess = self.useFixture(fixtures.MockPatch(
            'pypowervm.adapter.Session')).mock
        self.sysget = self.useFixture(fixtures.MockPatch(
            'pypowervm.wrappers.managed_system.System.get')).mock
        self.sys = mock.Mock()
        self.sysget.return_value = [self.sys]
        # For setup_rpc
        self.plg_rpt_st_api = self.useFixture(fixtures.MockPatch(
            'neutron.agent.rpc.PluginReportStateAPI')).mock
        self.gacwos = self.useFixture(fixtures.MockPatch(
            'neutron_lib.context.get_admin_context_without_session')).mock
        self.crt_cons = self.useFixture(fixtures.MockPatch(
            'neutron.agent.rpc.create_consumers')).mock
        self.filc = self.useFixture(fixtures.MockPatch(
            'oslo_service.loopingcall.FixedIntervalLoopingCall')).mock
        # For PluginAPI
        self.plug_api = self.useFixture(fixtures.MockPatch(
            'neutron.agent.rpc.PluginApi.__init__')).mock
        self.plug_api.return_value = None
        # For VIF event handler
        self.veh = self.useFixture(fixtures.MockPatch(
            'networking_powervm.plugins.ibm.agent.powervm.agent_base.'
            'VIFEventHandler')).mock
        pvm_par.validate_vios_ready = mock.Mock()


class BasePVMTestCase(base.BaseTestCase):
    """The base PowerVM Test case."""

    # TODO(edmondsw): When neutron-lib exposes a BaseTestClass we should
    # switch to that so that we are no longer importing from neutron, which
    # is prohibited by hacking rule N530
