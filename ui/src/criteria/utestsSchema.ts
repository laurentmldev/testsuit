// Copyright (C) 2026 Laurent Millet-Lacombe
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of testsuit.
// testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
// General Public License as published by the Free Software Foundation, either version 3 of the License,
// or (at your option) any later version.
// testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
// implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
// Public License for more details.
// You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
// see <https://www.gnu.org/licenses/>.

// JSON Schema of a utests.yml file (testsuit.exploit.mexploit.utest), for completion and hover help.
const STATUS = {
  description: "What the check must give on this case: SUCCESS, FAILED (its assertion failed), ERROR (it could not run), or any (not looked at).",
  anyOf: [
    { enum: ["SUCCESS", "FAILED", "ERROR", "any"] },
    {
      type: "object", additionalProperties: false, required: ["status"],
      properties: {
        status: { enum: ["SUCCESS", "FAILED", "ERROR", "any"], description: "The status expected." },
        message: { type: "string", description: "A regular expression searched in the failure message." },
      },
    },
  ],
};

const PATHS = { anyOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] };

export const UTESTS_SCHEMA = {
  $schema: "http://json-schema.org/draft-07/schema#",
  type: "object",
  additionalProperties: false,
  required: ["scenario", "cases"],
  properties: {
    description: { type: "string", description: "What these unit tests are about." },
    scenario: { type: "string", description: "The scenario folder (or file) under test, relative to this file." },
    dictionaries: { ...PATHS, description: "Dictionaries of keys rendering the scenario, as the datapack tool does (relative to this file)." },
    keys: { type: "object", description: "Keys set by hand, overriding the dictionaries (`scenario.duration_s: 30`)." },
    cases: {
      type: "object",
      description: "The cases: each runs the scenario on its data, in its own mxp process.",
      additionalProperties: {
        type: "object", additionalProperties: false, required: ["data"],
        properties: {
          description: { type: "string", description: "What the case shows." },
          data: { ...PATHS, description: "The data folder(s) of the case, relative to this file." },
          keys: { type: "object", description: "Keys of this case only." },
          dictionaries: { ...PATHS, description: "Dictionaries of this case only." },
          expect: {
            type: "object",
            description: "The status expected of each check, by entry name. A check not listed must succeed, unless `\"*\"` says otherwise.",
            additionalProperties: STATUS,
          },
        },
      },
      defaultSnippets: [{ label: "case", body: { "${1:name}": { description: "$2", data: "${1:name}/", expect: { "${3:check}": "FAILED" } } } }],
    },
  },
};
