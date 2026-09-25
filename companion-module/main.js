import { InstanceBase, InstanceStatus } from '@companion-module/base'

const BASE_DEFAULT = 'http://127.0.0.1:8789'
const CH_FIELD = { type: 'number', label: 'Input', id: 'ch', min: 1, max: 4, default: 1 }

class V1SDIInstance extends InstanceBase {
	constructor(internal) {
		super(internal)
		this.state = {}
		this.timer = null
	}

	async init(config) {
		this.config = config
		this.updateStatus(InstanceStatus.Connecting)
		this.setActionDefinitions(this.getActionDefinitions())
		this.setFeedbackDefinitions(this.getFeedbackDefinitions())
		this.startPolling()
	}

	getConfigFields() {
		return [
			{
				type: 'textinput',
				id: 'base',
				label: 'v1sdi-remote base URL',
				tooltip: 'Where the v1sdi_midi.py daemon listens',
				default: BASE_DEFAULT,
				width: 8,
			},
		]
	}

	async destroy() {
		if (this.timer) clearInterval(this.timer)
	}

	async apiGet(path) {
		const res = await fetch((this.config.base || BASE_DEFAULT) + path)
		if (!res.ok) throw new Error('HTTP ' + res.status)
		return res.json()
	}

	startPolling() {
		if (this.timer) clearInterval(this.timer)
		this.timer = setInterval(async () => {
			try {
				const j = await this.apiGet('/status')
				this.state = j.state || {}
				this.updateStatus(InstanceStatus.Ok)
				this.checkFeedbacks()
			} catch (e) {
				this.updateStatus(InstanceStatus.UnknownError, 'daemon unreachable: ' + e.message)
			}
		}, 1000)
	}

	setPresets() {
		const presets = {}

		const mk = (id, name, text, bgcolor, actionId, actionOpts, feedbackId, fbOpts, fbStyle) => {
			presets[id] = {
				name,
				type: 'simple',
				keywords: ['roland', 'v1sdi', 'switcher'],
				style: {
					text,
					size: 'auto',
					color: combineRgb(255, 255, 255),
					bgcolor,
				},
				steps: [
					{
						down: [{ actionId, options: actionOpts }],
						up: [],
					},
				],
				feedbacks: feedbackId
					? [{ feedbackId, options: fbOpts, style: fbStyle }]
					: [],
			}
		}

		const grey = combineRgb(45, 45, 45)
		const red = combineRgb(180, 30, 30)
		const green = combineRgb(30, 120, 30)
		const blue = combineRgb(30, 60, 160)
		const amber = combineRgb(170, 110, 20)

		// PGM 1-4 (red when live)
		for (let n = 1; n <= 4; n++) {
			mk(`pgm_${n}`, `PGM: take input ${n} to program`, `PGM ${n}`, grey,
				'pgm', { ch: n }, 'pgm_is', { ch: n }, { bgcolor: red })
		}
		// PST 1-4 (green when on preview)
		for (let n = 1; n <= 4; n++) {
			mk(`pst_${n}`, `PST: send input ${n} to preview`, `PST ${n}`, grey,
				'pst', { ch: n }, 'pst_is', { ch: n }, { bgcolor: green })
		}
		// Takes
		mk('auto', 'AUTO: timed take', 'AUTO', amber, 'auto', {}, null, null, null)
		mk('cut', 'CUT: instant take', 'CUT', red, 'cut', {}, null, null, null)
		// Transition types (blue when selected)
		mk('trs_wipe', 'Transition: Wipe', 'WIPE', grey, 'trs', { effect: '0' }, 'trs_is', { eff: 'wipe' }, { bgcolor: blue })
		mk('trs_mix', 'Transition: Mix', 'MIX', grey, 'trs', { effect: '1' }, 'trs_is', { eff: 'mix' }, { bgcolor: blue })
		mk('trs_cut', 'Transition: Cut', 'TRS CUT', grey, 'trs', { effect: '2' }, 'trs_is', { eff: 'cut' }, { bgcolor: blue })
		// Toggles (blue when active)
		mk('pip', 'PinP toggle', 'PinP', grey, 'pip', {}, 'pip_on', {}, { bgcolor: blue })
		mk('split', 'SPLIT toggle', 'SPLIT', grey, 'split', {}, 'split_on', {}, { bgcolor: blue })
		mk('dsk', 'DSK toggle', 'DSK', grey, 'dsk', {}, 'dsk_on', {}, { bgcolor: blue })
		mk('freeze', 'FREEZE input', 'FREEZE', grey, 'freeze', {}, null, null, null)
		// Memories 1-8
		for (let n = 1; n <= 8; n++) {
			mk(`mem_${n}`, `Memory ${n}`, `MEM ${n}`, grey, 'mem', { m: n - 1 }, null, null, null)
		}

		this.setPresetDefinitions(
			[
				{
					id: 'v1sdi_main',
					name: 'Roland V-1SDI',
					description: 'Switcher functions with live tally feedback',
					definitions: Object.keys(presets),
				},
			],
			presets,
		)
	}

	send(path) {
		return async () => {
			try {
				await this.apiGet(path)
				this.updateStatus(InstanceStatus.Ok)
			} catch (e) {
				this.updateStatus(InstanceStatus.UnknownError, e.message)
				this.log('error', 'v1sdi request failed: ' + e.message)
			}
		}
	}

	getActionDefinitions() {
		const chField = CH_FIELD
		return {
			pgm: { name: 'PGM: take input to program', options: [chField], callback: this.send('/pgm?ch=') },
			pst: { name: 'PST: send input to preview', options: [chField], callback: this.send('/pst?ch=') },
			auto: { name: 'AUTO (timed take)', options: [], callback: this.send('/auto') },
			cut: { name: 'CUT (instant take)', options: [], callback: this.send('/cut') },
			trs: {
				name: 'Transition type',
				options: [
					{
						type: 'dropdown',
						label: 'Effect',
						id: 'effect',
						choices: [
							{ id: '0', label: 'Wipe' },
							{ id: '1', label: 'Mix' },
							{ id: '2', label: 'Cut' },
						],
						default: '1',
					},
				],
				callback: this.send('/trs?effect='),
			},
			pip: { name: 'PinP toggle', options: [], callback: this.send('/pip') },
			split: { name: 'SPLIT toggle', options: [], callback: this.send('/split') },
			dsk: { name: 'DSK toggle', options: [], callback: this.send('/dsk') },
			freeze: { name: 'FREEZE', options: [], callback: this.send('/freeze') },
			mem: {
				name: 'Memory select',
				options: [
					{
						type: 'number',
						label: 'Memory (1-8)',
						id: 'm',
						min: 0,
						max: 7,
						default: 0,
					},
				],
				callback: this.send('/mem?m='),
			},
		}
	}

	getFeedbackDefinitions() {
		return {
			pgm_is: {
				type: 'boolean',
				name: 'Input is on PGM',
				description: 'Light while this input is on program',
				options: [CH_FIELD],
				callback: (fb) => this.state.pgm === fb.options.ch,
			},
			pst_is: {
				type: 'boolean',
				name: 'Input is on PST',
				description: 'Light while this input is on preview',
				options: [CH_FIELD],
				callback: (fb) => this.state.pst === fb.options.ch,
			},
			trs_is: {
				type: 'boolean',
				name: 'Transition type is',
				options: [
					{
						type: 'dropdown',
						label: 'Effect',
						id: 'eff',
						choices: [
							{ id: 'wipe', label: 'Wipe' },
							{ id: 'mix', label: 'Mix' },
							{ id: 'cut', label: 'Cut' },
						],
						default: 'cut',
					},
				],
				callback: (fb) => this.state.trs === fb.options.eff,
			},
			dsk_on: { type: 'boolean', name: 'DSK is on', options: [], callback: () => !!this.state.dsk },
			pip_on: { type: 'boolean', name: 'PinP is on', options: [], callback: () => !!this.state.pinp },
			split_on: { type: 'boolean', name: 'SPLIT is on', options: [], callback: () => !!this.state.split },
		}
	}
}

export default V1SDIInstance