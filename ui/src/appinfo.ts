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

// What the server of the testsuit app says about itself (GET app/info): its title and logo, the modes on in
// testsuit.toml, those this viewer can use (the exploit mode only from the machine testsuit runs on), and
// the state of the sequencer's tsqd.
export type SequencerStatus = {
  state: string; detail: string; url: string; managed: boolean; pid: number | null; restarts: number;
  last_exit: number | null; started_at: number | null; log: string | null; config: string | null;
};

export type AppInfo = {
  app: "testsuit"; testsuit: string; title: string; logo: string | null; modes: string[]; available: string[];
  local: boolean; stoppable: boolean; config: string | null; url: string; sequencer: SequencerStatus | null;
};
