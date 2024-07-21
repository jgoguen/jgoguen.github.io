package main

/*
unbound2influx
Copyright 2024 Joel Goguen

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
)

type Config struct {
	Token     string
	Bucket    string
	Org       string
	HostsFile string
	TargetURI string
}

func main() {
	var cfgFile string

	flag.StringVar(&cfgFile, "config", "/etc/unbound2influx.cfg", "Config file path")

	flag.Parse()

	f, err := os.Open(cfgFile)
	if err != nil {
		panic(err)
	}
	defer f.Close()

	cfg := &Config{
		Token:     "",
		Bucket:    "dns",
		Org:       "jgoguen",
		HostsFile: "",
		TargetURI: "",
	}
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			fmt.Fprintf(os.Stderr, "Invalid config line: %s\n", line)
			continue
		}

		key := strings.TrimSpace(parts[0])
		switch key {
		case "token":
			cfg.Token = strings.TrimSpace(parts[1])
		case "bucket":
			cfg.Bucket = strings.TrimSpace(parts[1])
		case "org":
			cfg.Org = strings.TrimSpace(parts[1])
		case "hosts":
			cfg.HostsFile = strings.TrimSpace(parts[1])
		case "influx_host":
			cfg.TargetURI = strings.TrimSpace(parts[1])
		}
	}

	if cfg.Org == "" || cfg.Bucket == "" || cfg.Token == "" || cfg.HostsFile == "" || cfg.TargetURI == "" {
		fmt.Fprintf(os.Stderr, "Must have values for target and token in config file %s\n", cfgFile)
		os.Exit(1)
	}

	hosts, err := ParseUnboundHosts(cfg.HostsFile)
	if err != nil {
		panic(err)
	}

	signals := make(chan os.Signal, 1)
	done := make(chan bool, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)

	messages := make(chan string, 255)
	wg := sync.WaitGroup{}

	wg.Add(2)
	go func() {
		defer wg.Done()

		select {
		case <-signals:
		case <-done:
		}

		close(messages)
	}()
	go ParseUnboundLog(&wg, messages, hosts, cfg)

	in := bufio.NewScanner(os.Stdin)
	for in.Scan() {
		messages <- in.Text()
	}
	if in.Err() != nil {
		panic(in.Err())
	}

	done <- true

	wg.Wait()
}

func ParseUnboundLog(wg *sync.WaitGroup, ch chan string, hosts map[string]string, cfg *Config) {
	defer wg.Done()

	t := &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 5 * time.Second,
		}).DialContext,
		MaxIdleConnsPerHost: 5,
	}
	httpClient := &http.Client{
		Transport: t,
	}

	for msg := range ch {
		parts := strings.Split(msg, " ")
		if len(parts) < 13 || parts[4] != "unbound:" || parts[6] != "info:" {
			continue
		}

		var record string
		if parts[8] == "static" || parts[8] == "always_nxdomain" {
			recordType := ""
			switch parts[8] {
			case "static":
				recordType = "unbound_static"
			case "always_nxdomain":
				recordType = "unbound_adblock"
			}

			clientParts := strings.SplitN(parts[9], "@", 2)
			clientIP := clientParts[0]
			client, ok := hosts[clientIP]
			if !ok {
				client = clientIP
			}

			record = fmt.Sprintf(
				"%s,host=%s,name=%s,type=%s,class=%s,client=%s,clientip=%s matched=1i\n",
				recordType,
				parts[3],
				parts[10],
				parts[11],
				parts[12],
				client,
				clientIP,
			)
		} else {
			clientIP := parts[7]
			client, ok := hosts[clientIP]
			if !ok {
				client = clientIP
			}

			record = fmt.Sprintf(
				"unbound_queries,host=%s,name=%s,type=%s,class=%s,clientip=%s,client=%s,return_code=%s,from_cache=%s time_to_resolve=%s,response_size=%si\n",
				parts[3],
				parts[8],
				parts[9],
				parts[10],
				clientIP,
				client,
				parts[11],
				parts[13],
				parts[12],
				parts[14],
			)
		}

		if record != "" {
			// Send the log to InfluxDB
			req, err := http.NewRequest("POST", fmt.Sprintf("%s/api/v2/write?org=%s&bucket=%s", cfg.TargetURI, cfg.Org, cfg.Bucket), strings.NewReader(record))
			if err != nil {
				fmt.Fprintf(os.Stderr, "Failed creating HTTP request: %v\n", err)
				continue
			}
			req.Header.Add("Authorization", fmt.Sprintf("Token %s", cfg.Token))
			req.Header.Add("Content-Type", "text/plain; charset=utf-8")
			req.Header.Add("Accept", "application/json")

			resp, err := httpClient.Do(req)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Failed making request to InfluxDB: %v\n", err)
				continue
			}

			if resp.StatusCode < 200 || resp.StatusCode >= 300 {
				fmt.Fprintf(os.Stderr, "Unexpected status code writing to InfluxDB: %d\n", resp.StatusCode)
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
	}
}

func ParseUnboundHosts(name string) (map[string]string, error) {
	hosts := make(map[string]string)

	hf, err := os.Open(name)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error opening %s: %v\n", name, err)
		return nil, err
	}
	defer hf.Close()

	dataPtrRE := regexp.MustCompile(`^local-data-ptr: "([^\s]+)\s+(.+)\."$`)
	dataRE := regexp.MustCompile(`^local-data: "(.+?)\.\s+(\w+)\s+(\w+)\s+(.+)"`)

	scanner := bufio.NewScanner(hf)
	for scanner.Scan() {
		line := scanner.Text()

		if strings.HasPrefix(line, "local-data-ptr:") {
			match := dataPtrRE.FindAllStringSubmatch(line, -1)
			if match == nil {
				continue
			}

			group := match[0]
			hosts[group[1]] = group[2]
		} else if strings.HasPrefix(line, "local-data:") {
			match := dataRE.FindAllStringSubmatch(line, -1)
			if match == nil {
				continue
			}

			group := match[0]
			hosts[group[4]] = group[1]
		}
	}

	return hosts, nil
}
