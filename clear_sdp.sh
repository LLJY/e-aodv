#!/bin/bash

# Delete duplicate Serial Port entries on channel 3, and any on other channels
# Keep only one entry for channel 3.  We'll arbitrarily keep the *first* one we find.

declare -a handles_to_delete
first_channel_3_handle=""

# Find the record handles to delete
while read -r line; do
  if [[ "$line" == *"Service RecHandle: "* ]]; then
    current_handle=$(echo "$line" | awk '{print $3}')
  elif [[ "$line" == *"Channel: 3"* ]]; then
    if [[ -z "$first_channel_3_handle" ]]; then
      first_channel_3_handle="$current_handle"
    else
      handles_to_delete+=("$current_handle")
    fi
  # Delete Serial Port on any other channel
  elif [[ "$line" == *"Channel: "* ]]; then
        channel=$(echo "$line" | awk '{print $2}')
        if [[ "$channel" != "3" &&  "$line" == *"RFCOMM"* ]]; then
           handles_to_delete+=("$current_handle")
        fi

  fi
done < <(sdptool browse local)


# Delete the identified handles
for handle in "${handles_to_delete[@]}"; do
  echo "Deleting record with handle: $handle"
  sudo sdptool del "$handle"
done

# Add back the Serial Port service on channel 3 (if it doesn't exist)
if [[ -z "$(sdptool browse local | grep 'Channel: 3')" ]]; then
  echo "Adding Serial Port service on channel 3..."
  sudo sdptool add --channel=3 SP
fi

# Verify the result
echo "Final SDP records:"
sdptool browse local

echo "Script complete."

exit 0