/** Browser/WebView entry point for the Drugref Reviewer Svelte application. */

import { mount } from "svelte";
import App from "./App.svelte";
import "./app.css";

/** Required DOM host declared by the application HTML shell. */
const target = document.getElementById("app");
if (!target) throw new Error("Drugref Reviewer could not find its application root");

/** Mounted Svelte application exported for development-tool inspection. */
const app = mount(App, { target });

export default app;
