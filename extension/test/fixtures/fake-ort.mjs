/**
 * A stand-in for ONNX Runtime, so worker.js's execution-provider ladder can be
 * tested without 88 MB of model and a GPU.
 *
 * It records which providers were asked for, in order, and can be told to make
 * one of them hang — which is the actual failure this exists to pin down.
 */

export const calls = [];

export const env = { wasm: {}, logLevel: 'error' };

/** Providers that never settle, mimicking a WebGPU build on a driverless host. */
export const hangs = new Set();
/** Providers that reject, mimicking an honest failure. */
export const throws = new Set();

export function reset() {
  calls.length = 0;
  hangs.clear();
  throws.clear();
}

export const InferenceSession = {
  async create(_model, { executionProviders }) {
    const ep = executionProviders[0];
    calls.push(ep);
    if (hangs.has(ep)) return new Promise(() => {});
    if (throws.has(ep)) throw new Error(`${ep} unavailable`);
    return {
      run: async () => ({ waveform: { data: new Float32Array(2400) } }),
      release: async () => {},
    };
  },
};

export const Tensor = class {
  constructor(type, data, dims) {
    Object.assign(this, { type, data, dims });
  }
};
