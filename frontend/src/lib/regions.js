/** The seven regions of the installation, in `lobe_id` order.
 *
 *  The order must match touchdesigner/exports/regions/regions.json and
 *  /lobes/activation: it is the contract described in docs/protocol.md.
 *
 *  The TRIGGER labels say what actually lights each area in this installation,
 *  not what would light it in theory.
 */

const ALL = [
  {
    key: 'frontal',
    code: 'L-F1',
    short: 'FRONTAL',
    name: 'Frontal lobe',
    trigger: 'TRIGGER: STRUCTURE',
    desc: 'Motor planning and executive control. Responds to musical structure and to the update of expectation at section boundaries.',
  },
  {
    key: 'parietal',
    code: 'L-P2',
    short: 'PARIETAL',
    name: 'Parietal lobe',
    trigger: 'TRIGGER: GROOVE',
    desc: 'Sensorimotor integration and spatial awareness. Tracks groove: the urge to move with the beat.',
  },
  {
    key: 'temporal',
    code: 'L-T3',
    short: 'TEMPORAL',
    name: 'Temporal lobe',
    trigger: 'TRIGGER: AUDIO',
    desc: 'Auditory and language processing. Extracts timbre, rhythm and melodic structure from the incoming signal.',
  },
  {
    key: 'occipital',
    code: 'L-O4',
    short: 'OCCIPITAL',
    name: 'Occipital lobe',
    trigger: 'TRIGGER: SUBJECT',
    desc: 'Primary visual processing. Driven by the webcam, not by the music: it responds to whoever is standing in front.',
  },
  {
    key: 'cerebellum',
    code: 'C-05',
    short: 'CEREBELLUM',
    name: 'Cerebellum',
    trigger: 'TRIGGER: BEAT',
    desc: 'Coordination and motor timing. Follows tempo and beat regularity rather than loudness.',
  },
  {
    key: 'brainstem',
    code: 'T-06',
    short: 'BRAINSTEM',
    name: 'Brainstem',
    trigger: 'TRIGGER: AROUSAL',
    desc: 'Fast subcortical pathway. Encodes signal periodicity and regulates overall arousal.',
  },
  {
    key: 'other_midline',
    code: 'M-07',
    short: 'MIDLINE',
    name: 'Midline structures',
    trigger: 'TRIGGER: CROSSTALK',
    desc: 'Connective white matter. Marks interhemispheric traffic when several areas work at once.',
  },
];

/** Regions not shown in the panel.
 *
 *  The midline structures stay in the model, the mapping and the OSC contract —
 *  they still light up in the render — but they are internal and would be
 *  hidden inside the others in a side view. Excluded from the dominant area for
 *  the same reason.
 */
export const HIDDEN = ['other_midline'];

/** The complete order, i.e. the contract: that of /lobes/activation and of
 *  touchdesigner/exports/regions/regions.json. For code reasoning by
 *  `lobe_id`, not for the UI. */
export const CONTRACT_REGIONS = ALL;

/** The ones shown in the panel. */
export const REGIONS = ALL.filter((r) => !HIDDEN.includes(r.key));

export const REGION_BY_KEY = Object.fromEntries(ALL.map((r) => [r.key, r]));
