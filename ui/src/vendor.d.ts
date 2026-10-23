declare module "monaco-editor/esm/vs/editor/edcore.main";
declare module "monaco-editor/esm/vs/basic-languages/*";
declare module "monaco-editor/esm/vs/editor/editor.worker?worker" {
  const Worker: { new (): Worker };
  export default Worker;
}
declare module "monaco-yaml/yaml.worker.js";
