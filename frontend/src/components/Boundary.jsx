import { Component } from 'react';

/** Isolates a failing panel from the rest of the screen.
 *
 *  An error in a React component unmounts the entire tree, so a single bad call
 *  can take the whole screen down, transport and library included. Each column
 *  is wrapped separately: the broken one shows an error box, the others keep
 *  working. The error still reaches the console.
 *
 *  Must be a class: error boundaries have no hook equivalent.
 */
export default class Boundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.name}] guasto nel pannello:`, error, info);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="boundary">
        <div className="boundary__title">PANEL UNAVAILABLE</div>
        <div className="boundary__hint">{this.props.name} — DETAILS IN CONSOLE</div>
      </div>
    );
  }
}
