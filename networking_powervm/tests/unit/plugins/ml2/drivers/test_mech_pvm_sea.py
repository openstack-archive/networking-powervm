# Copyright 2014 IBM Corp.
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

import mock

from networking_powervm.plugins.ml2.drivers import mech_pvm_sea as m_pvm
from networking_powervm.tests.unit.plugins.ibm.powervm import base

from neutron.plugins.ml2 import driver_api as api


class TestPvmMechDriver(base.BasePVMTestCase):

    def setUp(self):
        super(TestPvmMechDriver, self).setUp()
        self.mech_drv = m_pvm.PvmSEAMechanismDriver()

    @mock.patch('networking_powervm.plugins.ml2.drivers.mech_pvm_sea.'
                'PvmSEAMechanismDriver.get_mappings')
    def test_check_segment_for_agent(self, mappings):
        """Validates that the VLAN type is supported by the agent."""
        # Only test the flow where we have custom code.
        mappings.return_value = {}

        fake_segment = {api.NETWORK_TYPE: 'vlan'}
        self.assertTrue(self.mech_drv.check_segment_for_agent(fake_segment,
                                                              None))

        bad_segment = {api.NETWORK_TYPE: 'gre'}
        self.assertFalse(self.mech_drv.check_segment_for_agent(bad_segment,
                                                               None))

    @mock.patch('neutron.plugins.ml2.drivers.mech_agent.'
                'SimpleAgentMechanismDriverBase.'
                'try_to_bind_segment_for_agent', return_value=True)
    def test_try_to_bind_segment_for_agent(self, try_bind):
        fake_segment = {api.NETWORK_TYPE: 'vlan', api.SEGMENTATION_ID: '1000',
                        api.PHYSICAL_NETWORK: 'default'}
        fake_context = mock.MagicMock()
        self.mech_drv.rpc_publisher = mock.MagicMock()
        self.mech_drv.try_to_bind_segment_for_agent(
            fake_context, fake_segment, None)
        self.mech_drv.rpc_publisher.port_update.assert_called_with(
            fake_context._plugin_context, fake_context._port,
            'vlan', '1000', 'default')
