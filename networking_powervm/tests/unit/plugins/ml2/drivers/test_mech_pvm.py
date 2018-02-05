# Copyright 2014, 2018 IBM Corp.
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

from neutron_lib.plugins.ml2 import api

from networking_powervm.plugins.ml2.drivers import mech_pvm_sea
from networking_powervm.plugins.ml2.drivers import mech_pvm_sriov
from networking_powervm.tests.unit.plugins.ibm.powervm import base


class BaseTestPvmMechDriver(base.BasePVMTestCase):

    def setUp(self):
        super(BaseTestPvmMechDriver, self).setUp()
        rpc_p = mock.patch('neutron.plugins.ml2.rpc.AgentNotifierApi')
        self.mock_rpc = rpc_p.start()
        self.addCleanup(rpc_p.stop)

        self.context = mock.Mock(current={})
        self.segment = {api.ID: 'id', api.NETWORK_ID: 'network_id',
                        api.SEGMENTATION_ID: 'seg_id'}
        self.agent = {'host': 'agent_host',
                      'configurations': {
                          'bridge_mappings': {
                              'the_network': ['p1', 'p2'],
                              'the_other_network': ['p3', 'p4']},
                          'default_redundancy': '4',
                          'default_capacity': 'None'}}

    def verify_check_segment_for_agent(self, supp_net_types):
        """Validates that the VLAN type is supported by the agent.

        Covers get_allowed_network_types and physnet_in_mappings.

        :param supp_net_types: Set of network type strings (see
                               neutron_lib.constants.TYPE_*) supported by the
                               mech driver.
        """
        all_net_types = {'flat', 'vlan', 'geneve', 'gre', 'local', 'vxlan',
                         'none'}
        unsupp_net_types = all_net_types - supp_net_types
        seg = self.segment
        agt = self.agent
        for net_type in supp_net_types:
            # Fail for valid net type when network isn't in bridge mappings.
            seg[api.PHYSICAL_NETWORK] = 'bogus_network'
            seg[api.NETWORK_TYPE] = net_type
            self.assertFalse(self.mech_drv.check_segment_for_agent(seg, agt))
            # Succeed when the network is in the bridge mappings
            seg[api.PHYSICAL_NETWORK] = 'the_other_network'
            self.assertTrue(self.mech_drv.check_segment_for_agent(seg, agt))

        # Bogus network types fail even with a network in bridge mappings
        seg[api.PHYSICAL_NETWORK] = 'the_network'
        for net_type in unsupp_net_types:
            seg[api.NETWORK_TYPE] = net_type
            self.assertFalse(self.mech_drv.check_segment_for_agent(seg, agt))

    @mock.patch('neutron.plugins.ml2.drivers.mech_agent.'
                'SimpleAgentMechanismDriverBase.check_segment_for_agent')
    def bad_bind_segment_for_agent(self, mock_cs4a):
        # Binding fails when check_segment_for_agent fails
        mock_cs4a.return_value = False
        self.assertFalse(self.mech_drv.try_to_bind_segment_for_agent(
            'context', self.segment, 'agent'))
        mock_cs4a.assert_called_once_with(self.segment, 'agent')

    @mock.patch('neutron.plugins.ml2.drivers.mech_agent.'
                'SimpleAgentMechanismDriverBase.check_segment_for_agent')
    def good_bind_segment_for_agent(self, mock_cvd, mock_cs4a):
        # Binding succeeds, and calls set_binding and customize_vif_details,
        # when check_segment_for_agent succeeds
        mock_cs4a.return_value = True
        self.assertTrue(self.mech_drv.try_to_bind_segment_for_agent(
            self.context, self.segment, 'agent'))
        self.context.set_binding.assert_called_once_with(
            'id', self.mech_drv.vif_type, mock_cvd.return_value)
        mock_cvd.assert_called_once_with(self.context, self.segment, 'agent')

    def verify_vif_details(self):
        self.segment[api.SEGMENTATION_ID] = 'the_seg_id'
        ret = self.mech_drv.customize_vif_details(
            self.context, self.segment, self.agent)
        self.assertFalse(ret['port_filter'])
        self.assertEqual('the_seg_id', ret['vlan'])
        # With no segmentation ID, vlan is None
        del self.segment[api.SEGMENTATION_ID]
        ret = self.mech_drv.customize_vif_details(
            self.context, self.segment, self.agent)
        self.assertFalse(ret['port_filter'])
        self.assertIsNone(ret['vlan'])
        return ret


class TestPvmSeaMechDriver(BaseTestPvmMechDriver):
    def setUp(self):
        super(TestPvmSeaMechDriver, self).setUp()
        self.mech_drv = mech_pvm_sea.PvmSEAMechanismDriver()

    def test_init(self):
        self.assertEqual('PowerVM Shared Ethernet agent',
                         self.mech_drv.agent_type)
        self.assertEqual(['normal'], self.mech_drv.supported_vnic_types)
        self.assertEqual('pvm_sea', self.mech_drv.vif_type)
        self.assertEqual(self.mock_rpc.return_value,
                         self.mech_drv.rpc_publisher)

    def test_check_segment_for_agent(self):
        self.verify_check_segment_for_agent({'vlan'})

    @mock.patch('networking_powervm.plugins.ml2.drivers.mech_pvm_base.'
                'PvmMechanismDriverBase.customize_vif_details')
    def test_try_to_bind_segment_for_agent(self, mock_cvd):
        # Not bindable - no port_update
        self.bad_bind_segment_for_agent()
        self.mock_rpc.return_value.port_update.assert_not_called()

        # Bindable - port updated
        self.segment[api.NETWORK_TYPE] = 'net_type'
        self.segment[api.PHYSICAL_NETWORK] = 'phys_net'
        self.good_bind_segment_for_agent(mock_cvd)
        self.mock_rpc.return_value.port_update.assert_called_once_with(
            self.context._plugin_context, self.context._port, 'net_type',
            'seg_id', 'phys_net')

    def test_vif_details(self):
        self.verify_vif_details()


class TestPvmSriovMechDriver(BaseTestPvmMechDriver):
    def setUp(self):
        super(TestPvmSriovMechDriver, self).setUp()
        self.mech_drv = mech_pvm_sriov.PvmSRIOVMechanismDriver()

    def test_init(self):
        self.assertEqual('PowerVM SR-IOV Ethernet agent',
                         self.mech_drv.agent_type)
        self.assertEqual(['direct'], self.mech_drv.supported_vnic_types)
        self.assertEqual('pvm_sriov', self.mech_drv.vif_type)
        self.assertEqual(self.mock_rpc.return_value,
                         self.mech_drv.rpc_publisher)

    def test_check_segment_for_agent(self):
        self.verify_check_segment_for_agent({'vlan', 'flat'})

    @mock.patch('networking_powervm.plugins.ml2.drivers.mech_pvm_sriov.'
                'PvmSRIOVMechanismDriver.customize_vif_details')
    def test_try_to_bind_segment_for_agent(self, mock_cvd):
        self.bad_bind_segment_for_agent()
        self.good_bind_segment_for_agent(mock_cvd)

    def test_vif_details_defaults(self):
        # No profile in context, mismatched network in segment
        self.segment['physical_network'] = 'bogus_net'
        vif_dets = self.verify_vif_details()
        self.assertEqual('bogus_net', vif_dets['physical_network'])
        # No ports
        self.assertEqual([], vif_dets['physical_ports'])
        # default_redundancy from agent config
        self.assertEqual(4, vif_dets['redundancy'])
        # platform default capacity
        self.assertIsNone(vif_dets['capacity'])
        self.assertIsNone(vif_dets['maxcapacity'])

    def test_vif_details_default_capacity(self):
        # Ensure non-None capacity default comes through
        self.segment['physical_network'] = 'bogus_net'
        self.agent['configurations']['default_capacity'] = '0.04'
        vif_dets = self.verify_vif_details()
        self.assertEqual(0.04, vif_dets['capacity'])
        self.assertEqual('bogus_net', vif_dets['physical_network'])

    def test_vif_details_proper_values(self):
        # Now with proper values set in profile & segment
        self.segment['physical_network'] = 'the_other_network'
        self.context.current = {'binding:profile': {'vnic_required_vfs': "10",
                                                    'capacity': "0.16"}}
        vif_dets = self.verify_vif_details()
        self.assertEqual(['p3', 'p4'], vif_dets['physical_ports'])
        self.assertEqual(10, vif_dets['redundancy'])
        self.assertEqual(0.16, vif_dets['capacity'])
        self.assertEqual('the_other_network', vif_dets['physical_network'])
        # If no maximum capacity is specified in binding profile, vif should
        # contain maxcapacity as None
        self.assertIsNone(vif_dets['maxcapacity'])

    def test_vif_details_max_capacity(self):
        # Both capacity and maximum capacity can be specified in binding
        # profile. Ensure mechanism driver pushes maximum capacity into vif
        # details
        self.segment['physical_network'] = 'data1'
        self.context.current = {'binding:profile': {'vnic_required_vfs': "10",
                                                    'maxcapacity': "0.75",
                                                    'capacity': "0.16"}}
        vif_dets = self.verify_vif_details()
        self.assertEqual(10, vif_dets['redundancy'])
        self.assertEqual(0.16, vif_dets['capacity'])
        self.assertEqual(0.75, vif_dets['maxcapacity'])
