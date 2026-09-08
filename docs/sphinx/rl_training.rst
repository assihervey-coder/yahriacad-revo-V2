Passe RL d'entraînement (torch)
===============================

La passe d'entraînement renforce le cerveau IA **offline** avant le déploiement
du runtime : elle pré-entraîne le world model par régression supervisée puis
entraîne la policy de placement par *policy-gradient* (REINFORCE). torch est
une dépendance optionnelle — le runtime numpy continue de fonctionner sans.

.. code-block:: bash

   pip install torch --index-url https://download.pytorch.org/whl/cpu
   python -m backend.services.ai_engine.rl_agent.training.train

Les quatre étapes
-----------------

1. **Collecte** — des rollouts aléatoires dans
   :class:`~backend.services.ai_engine.rl_agent.training.env.PlacementEnv`
   produisent les transitions (12 features → 3 conséquences réelles) : le
   dataset n'est jamais fictif, il provient du simulateur interne déterministe.
2. **World model supervisé** —
   :class:`~backend.services.ai_engine.rl_agent.training.torch_modules.TorchWorldModel`
   reflète l'architecture exacte du ``TinyNet`` numpy du runtime (projection
   aléatoire fixe + tête linéaire) : entraîné avec MSE + Adam + early stopping,
   il est **exporté en ``.npz``** chargeable tel quel par
   :meth:`~backend.services.ai_engine.rl_agent.world_model.dreamer.DreamerWorldModel.load`.
3. **REINFORCE** —
   :class:`~backend.services.ai_engine.rl_agent.training.reinforce.ReinforceTrainer`
   entraîne la
   :class:`~backend.services.ai_engine.rl_agent.training.torch_modules.PlacementPolicyNet`
   (tête gaussienne pour dx/dy, catégorielles pour rotation/couche) avec
   baseline EMA et bonus d'entropie ; le meilleur checkpoint (retour moyen sur
   fenêtre glissante de 10 épisodes) est conservé.
4. **Artefacts** — ``data/trained_models/rl_checkpoints/`` :
   ``world_model_torch.npz``, ``policy_reinforce.pt``, ``training_report.json``.
   L'export npz est en plus installé au **slot runtime**
   ``PCB_WORLD_MODEL_NPZ`` (défaut ``data/trained_models/world_model_torch.npz``)
   où la boucle nocturne le chargera.

Branchement sur la boucle nocturne
----------------------------------

Le slot runtime relie la passe RL au runtime AutoPCB : à l'ouverture de chaque
nuit, :meth:`~backend.services.ai_engine.main.AiEngine.run_night_optimization`
charge ``world_model_torch.npz`` en **warm start** (si le fichier existe), puis
en **fin de nuit** y réécrit les poids mis à jour par les itérations gardées
(``observe`` + ``train`` du world model numpy, miroir exact). Chaque nuit
repart donc du modèle de la veille, et le bilan complet est exposé dans
``AiEngine.last_night_summary`` (warm start, score initial/final, gain, slot
réécrit). La sauvegarde passe dans un ``finally`` : elle s'exécute même si le
générateur est refermé avant épuisement.

.. code-block:: bash

   make nightly           # passe RL complète + boucle ratchet 300 itérations
   make nightly-quick     # smoke : RL courte + 60 itérations (~5 s)

La nuit du smoke test déplace le score composite de 0,544 à 0,6122
(+12,55 %, 5 keep / 1 reject) tout en respectant le ratchet (jamais de
régression). Sans torch, la passe RL est sautée et la nuit démarre des poids
courants — le reste de la chaîne est inchangé.

Environnement de placement
--------------------------

Chaque épisode place séquentiellement les composants non verrouillés d'une
carte. La récompense d'un pas est déterministe :

.. math::

   r = 0.6\,(1 - 0.4\,\text{congestion} - 0.3\,\text{thermique} - 0.3\,\text{SI})
     + 0.4\,\text{gain}_{HPWL} - 0.5\,\mathbb{1}_{\text{illégal}}

Une action illégale (hors carte, keepout, collision) n'est pas appliquée :
le composant garde sa position et l'agent encaisse la pénalité.

Hyperparamètres du CLI
----------------------

.. list-table::
   :header-rows: 1
   :widths: 28 20 52

   * - Option
     - Défaut
     - Rôle
   * - ``--episodes-dataset``
     - 40
     - épisodes de rollouts aléatoires pour le dataset
   * - ``--epochs-world``
     - 80
     - epochs max de la régression supervisée (early stopping inclus)
   * - ``--episodes-rl``
     - 150
     - épisodes REINFORCE
   * - ``--out``
     - ``data/trained_models/rl_checkpoints``
     - répertoire des artefacts
   * - ``--runtime-npz``
     - ``PCB_WORLD_MODEL_NPZ``
     - slot runtime du world model (warm start de la boucle nocturne)
   * - ``--no-install``
     - —
     - n'installe pas l'export au slot runtime
   * - ``--archive``
     - —
     - archive keeper JSONL résumée dans le rapport (contexte d'audit)
