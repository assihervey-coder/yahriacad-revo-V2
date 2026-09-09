Services gRPC
=============

Les sept services de calcul portés par le backend : parser, simulateur,
routeur, DRC/DFM, exporter, firmware bridge et la passerelle API.

Parser
------

.. automodule:: backend.services.parser.nl_to_skidl.nl_to_skidl

.. automodule:: backend.services.parser.constraint_extractor.extractor

.. automodule:: backend.services.parser.netlist_parser.kicad_sch_parser

Simulateur
----------

.. automodule:: backend.services.simulator.thermal_sim.diffusion

.. automodule:: backend.services.simulator.em_sim.quasi_static

.. automodule:: backend.services.simulator.signal_integrity.ir_drop

.. automodule:: backend.services.simulator.signal_integrity.eye_diagram

.. automodule:: backend.services.simulator.multi_physics_loop.loop

Routeur
-------

.. automodule:: backend.services.router.topological.connectivity_graph

.. automodule:: backend.services.router.topological.pathfinder

.. automodule:: backend.services.router.via_minimizer.minimizer

DRC / DFM
---------

.. automodule:: backend.services.drc_dfm_engine.design_rules.checker

.. automodule:: backend.services.drc_dfm_engine.manufacturing_rules.profiles

.. automodule:: backend.services.drc_dfm_engine.erc_executor.erc

Exporter
--------

.. automodule:: backend.services.exporter.gerber.rs274x

.. automodule:: backend.services.exporter.gerber.excellon

.. automodule:: backend.services.exporter.odb.odb_writer

.. automodule:: backend.services.exporter.bom_pickplace.bom_writer

Firmware bridge
---------------

.. automodule:: backend.services.firmware_bridge.pin_exporter.exporter

.. automodule:: backend.services.firmware_bridge.header_generator.generators

Passerelle API
--------------

.. automodule:: backend.api_gateway.main

.. automodule:: backend.api_gateway.middleware.auth

.. automodule:: backend.api_gateway.middleware.rate_limit
